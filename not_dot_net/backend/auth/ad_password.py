"""A user changing their own AD password, with no service account.

A valid password changes over LDAP, bound as the user: AD checks the old
password and applies its policy. An expired one (or "must change at next
logon") can't bind at all, so it goes through Kerberos kpasswd (RFC 3244),
which is what Windows does at its own "change your password" screen.
"""

import logging
from collections.abc import Callable
from urllib.parse import urlparse

from ldap3 import Connection
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars

from not_dot_net.backend.auth import login_throttle
from not_dot_net.backend.auth.ldap import (
    USERNAME_RE,
    BindFailure,
    LdapConfig,
    bind_failure,
    default_ldap_connect,
    password_expired,
)

__all__ = ["BindFailure", "PasswordChangeError", "bind_failure", "change_own_password", "kdc_host"]

logger = logging.getLogger("not_dot_net.ad_password")

_THROTTLE_SCOPE = "password-change"


class PasswordChangeError(Exception):
    """`reason` is an i18n-friendly code: bad_credentials, policy, locked,
    disabled, throttled or unavailable."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason


def kdc_host(cfg: LdapConfig) -> str:
    """The configured DC, which is also a KDC; the domain name when none is set."""
    first = cfg.url.split(",")[0].strip()
    if not first:
        return cfg.domain
    return urlparse(first if "://" in first else f"ldap://{first}").hostname or cfg.domain


def default_kpasswd(username: str, domain: str, new: str, old: str, kdc: str) -> None:
    from impacket.krb5.kpasswd import changePassword

    changePassword(username, domain.upper(), new, oldPasswd=old, kdcHost=kdc)


def change_own_password(
    username: str,
    old_password: str,
    new_password: str,
    cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
    kpasswd: Callable[..., None] = default_kpasswd,
) -> None:
    """Raise PasswordChangeError on any failure; return quietly on success."""
    if not USERNAME_RE.match(username):
        raise PasswordChangeError("bad_credentials")
    if login_throttle.retry_after_seconds(username, _THROTTLE_SCOPE):
        raise PasswordChangeError("throttled")
    try:
        conn = connect(cfg, username, old_password)
    except LDAPBindError as exc:
        if password_expired(exc):
            _change_through_kerberos(username, old_password, new_password, cfg, kpasswd)
            login_throttle.record_success(username, _THROTTLE_SCOPE)
            return
        login_throttle.record_failure(username, _THROTTLE_SCOPE)
        raise PasswordChangeError(_bind_reason(exc)) from exc
    except LDAPException as exc:
        logger.warning("AD unreachable for password change of '%s': %s", username, exc)
        raise PasswordChangeError("unavailable") from exc
    login_throttle.record_success(username, _THROTTLE_SCOPE)
    try:
        _change_over_ldap(conn, username, old_password, new_password, cfg)
    finally:
        conn.unbind()


def _bind_reason(exc: Exception) -> str:
    failure = bind_failure(exc)
    if failure in (BindFailure.LOCKED, BindFailure.DISABLED):
        return failure.value
    return "bad_credentials"


def _change_over_ldap(conn, username: str, old: str, new: str, cfg: LdapConfig) -> None:
    conn.search(cfg.base_dn, f"(sAMAccountName={escape_filter_chars(username)})", attributes=["cn"])
    if not conn.entries:
        raise PasswordChangeError("bad_credentials")
    if not conn.extend.microsoft.modify_password(conn.entries[0].entry_dn, new, old):
        logger.info("AD refused new password for '%s': %s", username, conn.result)
        raise PasswordChangeError("policy", str((conn.result or {}).get("message", "")))


def _change_through_kerberos(username: str, old: str, new: str, cfg: LdapConfig, kpasswd) -> None:
    from impacket.krb5.kpasswd import KPasswdError

    try:
        kpasswd(username, cfg.domain, new, old, kdc_host(cfg))
    except KPasswdError as exc:
        # AD already confirmed the old password (532/773 are only sent for a
        # right one), so a refusal here is about the new password.
        logger.info("Kerberos refused new password for '%s': %s", username, exc)
        raise PasswordChangeError("policy", str(exc)) from exc
    except Exception as exc:
        logger.warning("Kerberos password change failed for '%s': %s", username, exc)
        raise PasswordChangeError("unavailable", str(exc)) from exc
