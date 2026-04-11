from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, status
from ldap3 import Server, Connection, ALL
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars
from pydantic import BaseModel

from not_dot_net.backend.app_config import section
from not_dot_net.backend.db import get_user_db
from not_dot_net.backend.schemas import TokenResponse
from not_dot_net.backend.users import get_jwt_strategy

router = APIRouter(tags=["auth"])


class LdapConfig(BaseModel):
    url: str = "ldap://localhost"
    domain: str = "example.com"
    base_dn: str = "dc=example,dc=com"
    port: int = 389


ldap_config = section("ldap", LdapConfig, label="LDAP / Active Directory")


import re

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


class LDAPAuthRequest(BaseModel):
    username: str
    password: str


def default_ldap_connect(ldap_cfg: LdapConfig, username: str, password: str) -> Connection:
    """Create and bind an AD connection using user@domain."""
    server = Server(ldap_cfg.url, port=ldap_cfg.port, get_info=ALL)
    bind_user = f"{username}@{ldap_cfg.domain}"
    conn = Connection(server, user=bind_user, password=password, auto_bind=True)
    return conn


def ldap_authenticate(
    username: str,
    password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> str | None:
    """Bind to AD, search for mail by sAMAccountName. Returns email or None."""
    try:
        conn = connect(ldap_cfg, username, password)
    except LDAPBindError:
        return None
    except LDAPException:
        return None

    try:
        safe_username = escape_filter_chars(username)
        conn.search(
            ldap_cfg.base_dn,
            f"(sAMAccountName={safe_username})",
            attributes=["mail"],
        )
        if not conn.entries:
            return None
        mail_attr = getattr(conn.entries[0], "mail", None)
        return mail_attr.value if mail_attr is not None else None
    finally:
        conn.unbind()


_ldap_connect: Callable[..., Connection] = default_ldap_connect


def set_ldap_connect(fn: Callable[..., Connection]) -> None:
    """Override the LDAP connection factory (for testing)."""
    global _ldap_connect
    _ldap_connect = fn


@router.post("/auth/ldap", response_model=TokenResponse)
async def ldap_login(
    credentials: LDAPAuthRequest,
    user_db=Depends(get_user_db),
):
    if not _USERNAME_RE.match(credentials.username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username format",
        )

    cfg = await ldap_config.get()
    email = ldap_authenticate(credentials.username, credentials.password, cfg, _ldap_connect)

    if email is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid LDAP credentials or user not found",
        )

    user = await user_db.get_by_email(email)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No local user mapped to this LDAP account",
        )

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    return TokenResponse(access_token=token)
