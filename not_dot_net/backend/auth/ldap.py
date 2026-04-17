import logging
import re
import ssl
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ldap3 import Server, Connection, ALL, Tls, MODIFY_REPLACE
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars
from pydantic import BaseModel

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
}

_AD_ATTRIBUTES = list(AD_ATTR_MAP.values()) + ["givenName", "sn"]


class TlsMode(str, Enum):
    NONE = "none"
    LDAPS = "ldaps"
    START_TLS = "start_tls"


class LdapConfig(BaseModel):
    url: str = "ldap://localhost"
    domain: str = "example.com"
    base_dn: str = "dc=example,dc=com"
    port: int = 389
    tls_mode: TlsMode = TlsMode.NONE
    tls_verify: bool = True
    user_filter: str = ""
    auto_provision: bool = True


ldap_config = section("ldap", LdapConfig, label="LDAP / Active Directory")


USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


@dataclass(frozen=True)
class LdapUserInfo:
    email: str
    dn: str
    full_name: str | None = None
    given_name: str | None = None
    surname: str | None = None
    phone: str | None = None
    office: str | None = None
    title: str | None = None
    department: str | None = None


def _build_tls(ldap_cfg: LdapConfig) -> Tls | None:
    if ldap_cfg.tls_mode == TlsMode.NONE:
        return None
    validate = ssl.CERT_REQUIRED if ldap_cfg.tls_verify else ssl.CERT_NONE
    return Tls(validate=validate)


def default_ldap_connect(ldap_cfg: LdapConfig, username: str, password: str) -> Connection:
    """Create and bind an AD connection using user@domain.

    Supports plain LDAP, LDAPS (TLS on connect), and StartTLS (upgrade after connect).
    """
    use_ssl = ldap_cfg.tls_mode == TlsMode.LDAPS
    tls = _build_tls(ldap_cfg)
    server = Server(ldap_cfg.url, port=ldap_cfg.port, use_ssl=use_ssl, tls=tls, get_info=ALL)
    bind_user = f"{username}@{ldap_cfg.domain}"

    auto_bind = "TLS_BEFORE_BIND" if ldap_cfg.tls_mode == TlsMode.START_TLS else True
    return Connection(server, user=bind_user, password=password, auto_bind=auto_bind)


def _attr_value(entry, name: str) -> str | None:
    attr = getattr(entry, name, None)
    return attr.value if attr is not None else None


def ldap_authenticate(
    username: str,
    password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> LdapUserInfo | None:
    """Bind to AD, search for user attributes by sAMAccountName.

    Returns LdapUserInfo on success, None on auth failure or user not found.
    """
    try:
        conn = connect(ldap_cfg, username, password)
    except LDAPBindError:
        return None
    except LDAPException:
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
            return None
        entry = conn.entries[0]
        email = _attr_value(entry, "mail")
        if email is None:
            return None
        return LdapUserInfo(
            email=email,
            dn=entry.entry_dn,
            full_name=_attr_value(entry, "displayName"),
            given_name=_attr_value(entry, "givenName"),
            surname=_attr_value(entry, "sn"),
            phone=_attr_value(entry, "telephoneNumber"),
            office=_attr_value(entry, "physicalDeliveryOfficeName"),
            title=_attr_value(entry, "title"),
            department=_attr_value(entry, "department"),
        )
    finally:
        conn.unbind()


_ldap_connect: Callable[..., Connection] = default_ldap_connect


def set_ldap_connect(fn: Callable[..., Connection]) -> None:
    """Override the LDAP connection factory (for testing)."""
    global _ldap_connect
    _ldap_connect = fn


def get_ldap_connect() -> Callable[..., Connection]:
    return _ldap_connect


class LdapModifyError(Exception):
    """Raised when an AD modify fails (bind, permissions, or server error)."""


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
    try:
        conn = connect(ldap_cfg, bind_username, bind_password)
    except LDAPBindError as e:
        raise LdapModifyError(f"LDAP bind failed: {e}") from e
    except LDAPException as e:
        raise LdapModifyError(f"LDAP connection error: {e}") from e

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
    "email":      "email",
    "full_name":  "full_name",
    "phone":      "phone",
    "office":     "office",
    "title":      "title",
    "department": "team",
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
        await session.commit()


async def provision_ldap_user(user_info: LdapUserInfo, default_role: str) -> "User":
    """Create a local user from AD attributes. Returns the new User."""
    from not_dot_net.backend.db import User, AuthMethod, session_scope, get_user_db
    from not_dot_net.backend.schemas import UserCreate
    from not_dot_net.backend.users import get_user_manager
    from contextlib import asynccontextmanager

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                user = await user_manager.create(
                    UserCreate(
                        email=user_info.email,
                        password=uuid.uuid4().hex,  # random, unusable for local login
                        is_active=True,
                    )
                )
                user.auth_method = AuthMethod.LDAP
                user.full_name = user_info.full_name
                user.ldap_dn = user_info.dn
                user.role = default_role
                session.add(user)
                await session.commit()
                await session.refresh(user)
                logger.info("Auto-provisioned LDAP user '%s' with role '%s'", user.email, default_role)
                return user
