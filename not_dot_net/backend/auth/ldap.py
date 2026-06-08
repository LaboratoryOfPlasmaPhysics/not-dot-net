import asyncio
import logging
import re
import ssl
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from ldap3 import Server, ServerPool, Connection, ALL, Tls, MODIFY_REPLACE, MODIFY_ADD, MODIFY_DELETE, ROUND_ROBIN
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars
from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import section

logger = logging.getLogger("not_dot_net.ldap")

AD_ATTR_MAP: dict[str, str] = {
    # local field  -> AD attribute
    "email":      "mail",
    "full_name":  "displayName",
    "phone":      "telephoneNumber",
    "office":     "physicalDeliveryOfficeName",
    "title":      "title",
    "team":       "department",
    "company":    "company",
    "description": "description",
    "webpage":    "wWWHomePage",
}

# Read-only AD attributes (not in AD_ATTR_MAP because users can't write them back)
_AD_READ_ONLY = [
    "givenName", "sn", "userPrincipalName", "sAMAccountName",
    "memberOf", "uidNumber", "gidNumber",
    "userAccountControl", "accountExpires", "lastLogonTimestamp",
]

_AD_ATTRIBUTES = list(AD_ATTR_MAP.values()) + _AD_READ_ONLY


class TlsMode(str, Enum):
    NONE = "none"
    LDAPS = "ldaps"
    START_TLS = "start_tls"


class LdapConfig(BaseModel):
    url: str = Field(
        default="",
        description="Server URL(s), comma-separated. Leave empty to auto-discover from domain via DNS SRV.",
    )
    domain: str = "example.com"
    base_dn: str = "dc=example,dc=com"
    port: int = 389
    tls_mode: TlsMode = TlsMode.NONE
    tls_verify: bool = True
    user_filter: str = ""
    auto_provision: bool = True

    @property
    def effective_urls(self) -> list[str]:
        """Resolved server URLs — from config or DNS SRV auto-discovery."""
        raw = self.url.strip()
        if not raw:
            return _discover_servers_from_dns(self.domain, self.tls_mode)
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        scheme = "ldaps" if self.tls_mode == TlsMode.LDAPS else "ldap"
        return [
            u if u.startswith(("ldap://", "ldaps://")) else f"{scheme}://{u}"
            for u in urls
        ]

    @property
    def effective_url(self) -> str:
        """First resolved URL (for display / backwards compat)."""
        urls = self.effective_urls
        return urls[0] if urls else f"ldap://{self.domain}"


ldap_config = section("ldap", LdapConfig, label="LDAP / Active Directory")


USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


_ACCOUNTDISABLE = 0x2
_ACCOUNT_EXPIRES_NEVER = {0, 9223372036854775807}
_FILETIME_EPOCH = datetime(1601, 1, 1)


def _ad_account_active(entry) -> bool:
    """Check userAccountControl + accountExpires to determine if the AD account is active."""
    uac = _attr_int(entry, "userAccountControl")
    if uac is not None and uac & _ACCOUNTDISABLE:
        return False
    attr = getattr(entry, "accountExpires", None)
    if attr is not None:
        val = attr.value
        if val is not None:
            if isinstance(val, datetime):
                # ldap3 auto-converts FILETIME to datetime; sentinel values
                # (0 → 1601-01-01, max → 9999+) mean "never expires"
                if val.year > 1601 and val.year < 9999:
                    now = datetime.now(timezone.utc)
                    expires = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
                    if expires < now:
                        return False
            elif isinstance(val, int) and val not in _ACCOUNT_EXPIRES_NEVER:
                expires_dt = _FILETIME_EPOCH + timedelta(microseconds=val // 10)
                if expires_dt < datetime.now(timezone.utc).replace(tzinfo=None):
                    return False
    return True


@dataclass(frozen=True)
class LdapUserInfo:
    email: str
    dn: str
    sam_account_name: str | None = None
    full_name: str | None = None
    given_name: str | None = None
    surname: str | None = None
    phone: str | None = None
    office: str | None = None
    title: str | None = None
    department: str | None = None
    company: str | None = None
    description: str | None = None
    webpage: str | None = None
    member_of: list[str] | None = None
    uid_number: int | None = None
    gid_number: int | None = None
    last_logon_timestamp: datetime | None = None
    is_active: bool = True


def _discover_servers_from_dns(domain: str, tls_mode: TlsMode) -> list[str]:
    """Discover AD domain controllers via DNS SRV records."""
    import dns.resolver

    service = "_ldaps._tcp" if tls_mode == TlsMode.LDAPS else "_ldap._tcp"
    scheme = "ldaps" if tls_mode == TlsMode.LDAPS else "ldap"
    try:
        answers = dns.resolver.resolve(f"{service}.{domain}", "SRV")
        records = sorted(answers, key=lambda r: (r.priority, -r.weight))
        return [f"{scheme}://{r.target.to_text().rstrip('.')}" for r in records]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        logger.info("DNS SRV lookup for %s.%s failed, falling back to domain name", service, domain)
        return [f"{scheme}://{domain}"]


def _build_tls(ldap_cfg: LdapConfig) -> Tls | None:
    if ldap_cfg.tls_mode == TlsMode.NONE:
        return None
    validate = ssl.CERT_REQUIRED if ldap_cfg.tls_verify else ssl.CERT_NONE
    return Tls(validate=validate)


def _build_server_or_pool(ldap_cfg: LdapConfig) -> Server | ServerPool:
    """Build a Server (single URL) or ServerPool (multiple URLs / SRV discovery)."""
    use_ssl = ldap_cfg.tls_mode == TlsMode.LDAPS
    tls = _build_tls(ldap_cfg)
    urls = ldap_cfg.effective_urls
    if not urls:
        raise LDAPException(f"No LDAP servers found for domain '{ldap_cfg.domain}'")
    servers = [Server(url, port=ldap_cfg.port, use_ssl=use_ssl, tls=tls, get_info=ALL) for url in urls]
    if len(servers) == 1:
        return servers[0]
    pool = ServerPool(servers, ROUND_ROBIN, active=True, exhaust=True)
    return pool


def default_ldap_connect(ldap_cfg: LdapConfig, username: str, password: str) -> Connection:
    """Create and bind an AD connection using user@domain.

    Supports plain LDAP, LDAPS (TLS on connect), and StartTLS (upgrade after connect).
    Uses ServerPool for failover when multiple servers are configured or discovered via DNS.
    """
    server = _build_server_or_pool(ldap_cfg)
    bind_user = f"{username}@{ldap_cfg.domain}"
    auto_bind = "TLS_BEFORE_BIND" if ldap_cfg.tls_mode == TlsMode.START_TLS else True
    return Connection(server, user=bind_user, password=password, auto_bind=auto_bind)


def _attr_value(entry, name: str) -> str | None:
    attr = getattr(entry, name, None)
    return attr.value if attr is not None else None


def _attr_list(entry, name: str) -> list[str] | None:
    attr = getattr(entry, name, None)
    if attr is None:
        return None
    vals = attr.values if hasattr(attr, "values") else attr.value
    if isinstance(vals, list):
        return [str(v) for v in vals] if vals else None
    return [str(vals)] if vals else None


def _attr_bytes(entry, name: str) -> bytes | None:
    attr = getattr(entry, name, None)
    if attr is None:
        return None
    val = attr.value
    return val if isinstance(val, bytes) else None


def _attr_int(entry, name: str) -> int | None:
    attr = getattr(entry, name, None)
    if attr is None:
        return None
    val = attr.value
    return int(val) if val is not None else None


def _attr_filetime(entry, name: str) -> datetime | None:
    """Parse an AD FILETIME attribute (e.g. lastLogonTimestamp) to UTC datetime.

    ldap3 sometimes auto-converts FILETIME to datetime; otherwise the raw
    value is a 64-bit int counting 100-ns intervals since 1601-01-01 UTC.
    Sentinel 0 / max-int means "never" — returns None.
    """
    attr = getattr(entry, name, None)
    if attr is None:
        return None
    val = attr.value
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.year <= 1601 or val.year >= 9999:
            return None
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, int):
        if val in _ACCOUNT_EXPIRES_NEVER:
            return None
        return (_FILETIME_EPOCH + timedelta(microseconds=val // 10)).replace(tzinfo=timezone.utc)
    return None


def ldap_authenticate(
    username: str,
    password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> tuple[LdapUserInfo, Connection] | None:
    """Bind to AD, search for user attributes by sAMAccountName.

    Returns (LdapUserInfo, bound Connection) on success, None on auth failure.
    The caller owns the connection and must unbind or store it.
    """
    try:
        conn = connect(ldap_cfg, username, password)
    except LDAPBindError:
        logger.debug("LDAP bind failed for '%s' (bad credentials)", username)
        return None
    except LDAPException as exc:
        logger.warning("LDAP connection error for '%s': %s", username, exc)
        return None

    try:
        safe_username = escape_filter_chars(username)
        account_filter = f"(sAMAccountName={safe_username})"
        if ldap_cfg.user_filter:
            search_filter = f"(&{account_filter}{ldap_cfg.user_filter})"
        else:
            search_filter = account_filter
        conn.search(
            ldap_cfg.base_dn,
            search_filter,
            attributes=_AD_ATTRIBUTES,
        )
        if not conn.entries:
            logger.warning("LDAP search found no entries for '%s' (filter: %s)", username, search_filter)
            conn.unbind()
            return None
        entry = conn.entries[0]
        info = _entry_to_user_info(entry, fallback_email=f"{username}@{ldap_cfg.domain}")
        if info is None:
            conn.unbind()
            return None
        return info, conn
    except Exception:
        conn.unbind()
        raise


_ldap_connect: Callable[..., Connection] = default_ldap_connect

# --- Per-user persistent LDAP connections with TTL ---

CONNECTION_TTL_SECONDS = 30 * 60  # 30 minutes
_REAP_INTERVAL_SECONDS = 60

_user_connections: dict[str, tuple[Connection, float]] = {}
_reaper_task: asyncio.Task | None = None


def _unbind_safe(conn: Connection) -> None:
    try:
        if conn.bound:
            conn.unbind()
    except Exception:
        pass


def store_user_connection(user_id: str, conn: Connection) -> None:
    old = _user_connections.pop(user_id, None)
    if old is not None:
        _unbind_safe(old[0])
    _user_connections[user_id] = (conn, time.monotonic())


def get_user_connection(user_id: str) -> Connection | None:
    entry = _user_connections.get(user_id)
    if entry is None:
        return None
    conn, stored_at = entry
    if time.monotonic() - stored_at > CONNECTION_TTL_SECONDS:
        _user_connections.pop(user_id, None)
        _unbind_safe(conn)
        return None
    if not conn.bound:
        _user_connections.pop(user_id, None)
        return None
    return conn


def drop_user_connection(user_id: str) -> None:
    entry = _user_connections.pop(user_id, None)
    if entry is not None:
        _unbind_safe(entry[0])


def drop_all_connections() -> None:
    for entry in _user_connections.values():
        _unbind_safe(entry[0])
    _user_connections.clear()


async def _reap_expired_connections() -> None:
    while True:
        await asyncio.sleep(_REAP_INTERVAL_SECONDS)
        now = time.monotonic()
        expired = [
            uid for uid, (_, stored_at) in _user_connections.items()
            if now - stored_at > CONNECTION_TTL_SECONDS
        ]
        for uid in expired:
            entry = _user_connections.pop(uid, None)
            if entry is not None:
                _unbind_safe(entry[0])
                logger.debug("Reaped expired LDAP connection for user %s", uid)


def start_connection_reaper() -> None:
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reap_expired_connections())


def set_ldap_connect(fn: Callable[..., Connection]) -> None:
    """Override the LDAP connection factory (for testing)."""
    global _ldap_connect
    _ldap_connect = fn


def get_ldap_connect() -> Callable[..., Connection]:
    return _ldap_connect


class LdapModifyError(Exception):
    """Raised when an AD modify fails (bind, permissions, or server error)."""


def _ldap_bind(
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> Connection:
    """Bind to AD. Raises LdapModifyError on failure."""
    try:
        return connect(ldap_cfg, bind_username, bind_password)
    except LDAPBindError as e:
        raise LdapModifyError(f"LDAP bind failed: {e}") from e
    except LDAPException as e:
        raise LdapModifyError(f"LDAP connection error: {e}") from e


def _query_writable_attributes(conn: Connection, dn: str) -> set[str]:
    """Query allowedAttributesEffective on an existing connection."""
    conn.search(dn, "(objectClass=*)", attributes=["allowedAttributesEffective"])
    if not conn.entries:
        return set()
    attr = conn.entries[0].allowedAttributesEffective
    values = attr.values if hasattr(attr, "values") else (attr.value or [])
    return {str(v) for v in values} if values else set()


def ldap_get_writable_attributes(
    dn: str,
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> set[str]:
    """Return the set of AD attribute names the bound user can write on `dn`."""
    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        return _query_writable_attributes(conn, dn)
    finally:
        conn.unbind()


def ldap_check_and_modify(
    dn: str,
    changes: dict[str, str | None],
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> tuple[set[str], list[str]]:
    """Single-connection: query writable attrs, filter, then modify.

    Returns (writable_attrs, skipped_attrs).
    Raises LdapModifyError on bind or modify failure.
    """
    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        writable = _query_writable_attributes(conn, dn)
        allowed = {attr: val for attr, val in changes.items() if attr in writable}
        skipped = [attr for attr in changes if attr not in writable]
        if allowed:
            modify_payload = {
                attr: [(MODIFY_REPLACE, [value] if value else [])]
                for attr, value in allowed.items()
            }
            ok = conn.modify(dn, modify_payload)
            if not ok:
                raise LdapModifyError(
                    f"modify failed: {conn.result.get('description')} "
                    f"({conn.result.get('message')})"
                )
        return writable, skipped
    finally:
        conn.unbind()


def ldap_set_account_enabled(
    dn: str,
    enabled: bool,
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> None:
    """Bind as bind_username and toggle the ACCOUNTDISABLE bit on dn.

    Reads the current userAccountControl, OR/AND the ACCOUNTDISABLE bit
    (0x2), and writes the new value back via MODIFY_REPLACE. Other UAC
    flags (DONT_EXPIRE_PASSWORD, PASSWD_NOTREQD, etc.) are preserved.
    No-op when the desired state already matches.
    """
    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        ok = conn.search(dn, "(objectClass=*)", attributes=["userAccountControl"])
        if not ok or not conn.entries:
            raise LdapModifyError(f"User not found: {dn}")
        current = conn.entries[0].userAccountControl.value
        if current is None:
            raise LdapModifyError(f"userAccountControl not readable for {dn}")
        current_int = int(current)
        new_int = (current_int & ~_ACCOUNTDISABLE) if enabled else (current_int | _ACCOUNTDISABLE)
        if new_int == current_int:
            return
        ok = conn.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [str(new_int)])]})
        if not ok:
            raise LdapModifyError(
                f"modify failed: {conn.result.get('description')} "
                f"({conn.result.get('message')})"
            )
    finally:
        conn.unbind()


def ldap_user_exists_by_sam(
    sam: str,
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> bool:
    """Return True if a user with this sAMAccountName exists in AD."""
    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        ok = conn.search(
            ldap_cfg.base_dn,
            f"(sAMAccountName={escape_filter_chars(sam)})",
            attributes=["sAMAccountName"],
        )
        return bool(ok and conn.entries)
    finally:
        conn.unbind()


def ldap_modify_user(
    dn: str,
    changes: dict[str, str | None],
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> None:
    """Bind as bind_username and replace attributes on dn.

    `changes` maps AD attribute name to new value. `None` clears the attribute.
    Raises LdapModifyError on bind failure, insufficient rights, or server error.
    """
    if not changes:
        return
    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        modify_payload = {
            attr: [(MODIFY_REPLACE, [value] if value else [])]
            for attr, value in changes.items()
        }
        ok = conn.modify(dn, modify_payload)
        if not ok:
            raise LdapModifyError(
                f"modify failed: {conn.result.get('description')} "
                f"({conn.result.get('message')})"
            )
    finally:
        conn.unbind()


# LdapUserInfo field name -> User model field name
_INFO_TO_USER: dict[str, str] = {
    "email":             "email",
    "sam_account_name":  "ldap_username",
    "full_name":         "full_name",
    "phone":             "phone",
    "office":            "office",
    "title":             "title",
    "department":        "team",
    "company":           "company",
    "description":       "description",
    "webpage":           "webpage",
    "member_of":         "member_of",
    "uid_number":        "uid_number",
    "gid_number":        "gid_number",
    "last_logon_timestamp": "last_ad_logon",
}


async def sync_user_from_ldap(user_id: uuid.UUID, info: LdapUserInfo) -> None:
    """Overwrite the AD-backed fields of a local user from a freshly-read AD entry.

    Local-only fields (employment_status, start_date, end_date) are untouched.
    """
    from not_dot_net.backend.db import User, session_scope

    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            return
        for info_field, user_field in _INFO_TO_USER.items():
            setattr(user, user_field, getattr(info, info_field))
        user.ldap_dn = info.dn
        user.is_active = info.is_active
        await session.commit()


async def provision_ldap_user(user_info: LdapUserInfo, default_role: str) -> "User":
    """Create a local user from AD attributes. Returns the new User.

    Bypasses UserCreate/UserManager to avoid EmailStr validation — AD
    domains like .local are valid internally but rejected by pydantic.
    """
    from not_dot_net.backend.db import User, AuthMethod, session_scope

    async with session_scope() as session:
        fields = {user_field: getattr(user_info, info_field)
                  for info_field, user_field in _INFO_TO_USER.items()}
        user = User(
            **fields,
            hashed_password="!ldap-no-local-password",
            auth_method=AuthMethod.LDAP,
            ldap_dn=user_info.dn,
            role=default_role,
            is_active=user_info.is_active,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info("Auto-provisioned LDAP user '%s' with role '%s'", user.email, default_role)
        return user


def _entry_to_user_info(entry, fallback_email: str | None = None) -> LdapUserInfo | None:
    """Parse a single ldap3 search entry into LdapUserInfo. Returns None if no email."""
    email = (
        _attr_value(entry, "mail")
        or _attr_value(entry, "userPrincipalName")
        or fallback_email
    )
    if not email:
        return None
    return LdapUserInfo(
        email=email,
        dn=entry.entry_dn,
        sam_account_name=_attr_value(entry, "sAMAccountName"),
        full_name=_attr_value(entry, "displayName"),
        given_name=_attr_value(entry, "givenName"),
        surname=_attr_value(entry, "sn"),
        phone=_attr_value(entry, "telephoneNumber"),
        office=_attr_value(entry, "physicalDeliveryOfficeName"),
        title=_attr_value(entry, "title"),
        department=_attr_value(entry, "department"),
        company=_attr_value(entry, "company"),
        description=_attr_value(entry, "description"),
        webpage=_attr_value(entry, "wWWHomePage"),
        member_of=_attr_list(entry, "memberOf"),
        uid_number=_attr_int(entry, "uidNumber"),
        gid_number=_attr_int(entry, "gidNumber"),
        last_logon_timestamp=_attr_filetime(entry, "lastLogonTimestamp"),
        is_active=_ad_account_active(entry),
    )


@dataclass
class SyncResult:
    synced: int = 0
    provisioned: int = 0
    skipped: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


async def sync_all_from_ldap(
    bind_username: str,
    bind_password: str,
) -> SyncResult:
    """Search AD for all users and sync/provision them locally.

    Returns counts of synced, provisioned, and skipped entries.
    """
    from not_dot_net.backend.db import User, AuthMethod, session_scope
    from not_dot_net.backend.roles import roles_config
    from sqlalchemy import select

    cfg = await ldap_config.get()
    conn = _ldap_bind(bind_username, bind_password, cfg, _ldap_connect)

    search_filter = cfg.user_filter or "(&(objectCategory=person)(objectClass=user))"
    try:
        conn.search(
            cfg.base_dn,
            search_filter,
            attributes=_AD_ATTRIBUTES,
            paged_size=500,
        )
    except LDAPException as e:
        conn.unbind()
        raise LdapModifyError(f"LDAP search failed: {e}") from e

    entries = list(conn.entries)

    cookie = conn.result.get("controls", {}).get("1.2.840.113556.1.4.319", {}).get("value", {}).get("cookie")
    while cookie:
        conn.search(
            cfg.base_dn,
            search_filter,
            attributes=_AD_ATTRIBUTES,
            paged_size=500,
            paged_cookie=cookie,
        )
        entries.extend(conn.entries)
        cookie = conn.result.get("controls", {}).get("1.2.840.113556.1.4.319", {}).get("value", {}).get("cookie")

    conn.unbind()

    async with session_scope() as session:
        existing = await session.execute(select(User))
        users_by_email = {u.email.lower(): u for u in existing.scalars().all()}

    roles_cfg = await roles_config.get()
    default_role = roles_cfg.default_role or ""
    result = SyncResult()

    for entry in entries:
        info = _entry_to_user_info(entry)
        if info is None:
            result.skipped += 1
            continue
        try:
            existing_user = users_by_email.get(info.email.lower())
            if existing_user is not None:
                await sync_user_from_ldap(existing_user.id, info)
                result.synced += 1
            else:
                new_user = await provision_ldap_user(info, default_role)
                users_by_email[info.email.lower()] = new_user
                result.provisioned += 1
        except Exception as e:
            result.errors.append(f"{info.email}: {e}")
            logger.warning("Failed to sync LDAP user '%s': %s", info.email, e)

    return result


@dataclass(frozen=True)
class NewAdUser:
    sam_account: str
    given_name: str
    surname: str
    display_name: str
    mail: str
    description: str | None
    ou_dn: str
    uid_number: int
    gid_number: int
    login_shell: str
    home_directory: str
    initial_password: str
    must_change_password: bool = True


def _ad_encode_password(plain: str) -> bytes:
    """AD requires unicodePwd as UTF-16LE of the quoted password."""
    return f'"{plain}"'.encode("utf-16-le")


_UAC_NORMAL_ACCOUNT = 0x200
_UAC_NORMAL_ACCOUNT_DISABLED = 0x202


def ldap_create_user(
    new_user: NewAdUser,
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> str:
    """Create a new AD user and return its DN.

    Order: add disabled → set password → optionally set pwdLastSet=0 → enable account.
    Raises LdapModifyError on any failure.
    """
    dn = f"CN={new_user.display_name},{new_user.ou_dn}"
    object_class = ["top", "person", "organizationalPerson", "user"]
    attrs = {
        "sAMAccountName": new_user.sam_account,
        "userPrincipalName": f"{new_user.sam_account}@{ldap_cfg.domain}",
        "givenName": new_user.given_name,
        "sn": new_user.surname,
        "displayName": new_user.display_name,
        "cn": new_user.display_name,
        "mail": new_user.mail,
        "uidNumber": new_user.uid_number,
        "gidNumber": new_user.gid_number,
        "loginShell": new_user.login_shell,
        "unixHomeDirectory": new_user.home_directory,
        "userAccountControl": str(_UAC_NORMAL_ACCOUNT_DISABLED),
    }
    if new_user.description:
        attrs["description"] = new_user.description

    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        ok = conn.add(dn, object_class, attrs)
        if not ok:
            raise LdapModifyError(
                f"add failed: {conn.result.get('description')} ({conn.result.get('message')})"
            )

        ok = conn.modify(dn, {"unicodePwd": [(MODIFY_REPLACE, [_ad_encode_password(new_user.initial_password)])]})
        if not ok:
            raise LdapModifyError(
                f"set password failed: {conn.result.get('description')} ({conn.result.get('message')})"
            )

        if new_user.must_change_password:
            ok = conn.modify(dn, {"pwdLastSet": [(MODIFY_REPLACE, ["0"])]})
            if not ok:
                raise LdapModifyError(
                    f"pwdLastSet failed: {conn.result.get('description')} ({conn.result.get('message')})"
                )

        ok = conn.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [str(_UAC_NORMAL_ACCOUNT)])]})
        if not ok:
            raise LdapModifyError(
                f"enable failed: {conn.result.get('description')} ({conn.result.get('message')})"
            )
    finally:
        conn.unbind()
    return dn


def _modify_group_member(
    op_kind,  # MODIFY_ADD or MODIFY_DELETE
    user_dn: str,
    group_dns: list[str],
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> dict[str, str]:
    """Internal helper: add or remove user_dn from group 'member' attributes.

    Returns {failed_group_dn: error_message} for any groups that failed.
    """
    failures: dict[str, str] = {}
    if not group_dns:
        return failures
    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        for gdn in group_dns:
            ok = conn.modify(gdn, {"member": [(op_kind, [user_dn])]})
            if not ok:
                failures[gdn] = (
                    f"{conn.result.get('description')} ({conn.result.get('message')})"
                )
    finally:
        conn.unbind()
    return failures


def ldap_add_to_groups(
    user_dn: str,
    group_dns: list[str],
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> dict[str, str]:
    """Add user_dn to each group's 'member' attribute. Returns {failed_group_dn: msg}."""
    return _modify_group_member(MODIFY_ADD, user_dn, group_dns, bind_username, bind_password, ldap_cfg, connect)


def ldap_remove_from_groups(
    user_dn: str,
    group_dns: list[str],
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> dict[str, str]:
    """Remove user_dn from each group's 'member' attribute. Returns {failed_group_dn: msg}."""
    return _modify_group_member(MODIFY_DELETE, user_dn, group_dns, bind_username, bind_password, ldap_cfg, connect)


@dataclass(frozen=True)
class GroupSummary:
    dn: str
    cn: str
    description: str | None


def ldap_list_groups(
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    *,
    base_dn: str | None = None,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> list[GroupSummary]:
    """Paged search for (objectClass=group). Returns [{dn, cn, description}]."""
    search_base = base_dn or ldap_cfg.base_dn
    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        ok = conn.search(
            search_base,
            "(objectClass=group)",
            attributes=["cn", "description"],
            paged_size=500,
        )
        if not ok:
            return []
        return [
            GroupSummary(
                dn=entry.entry_dn,
                cn=_attr_value(entry, "cn") or entry.entry_dn,
                description=_attr_value(entry, "description"),
            )
            for entry in conn.entries
        ]
    finally:
        conn.unbind()
