"""S2 — POST /auth/login must throttle repeated failures.

Nothing limited attempts, and the LDAP fallback binds to AD with whatever
username/password arrives, so an unauthenticated attacker could spray passwords
against real AD accounts through the intranet and trip AD-side lockouts.

Throttling is per (username, IP): the AD-lockout DoS is the real exposure, and
locking purely by IP would let one NATed office lock out everyone behind it.
"""
import pytest

from not_dot_net.backend.auth import login_throttle
from not_dot_net.backend.auth.login_throttle import (
    MAX_FAILURES,
    MAX_IP_FAILURES,
    record_failure,
    record_success,
    retry_after_seconds,
)


@pytest.fixture(autouse=True)
def clean_slate():
    login_throttle.reset()
    yield
    login_throttle.reset()


def test_fresh_key_is_not_throttled():
    assert retry_after_seconds("alice", "10.0.0.1") == 0


def test_throttles_after_max_failures():
    for _ in range(MAX_FAILURES):
        record_failure("alice", "10.0.0.1")
    assert retry_after_seconds("alice", "10.0.0.1") > 0


def test_below_the_limit_is_still_allowed():
    for _ in range(MAX_FAILURES - 1):
        record_failure("alice", "10.0.0.1")
    assert retry_after_seconds("alice", "10.0.0.1") == 0


def test_other_users_are_unaffected():
    """One account's lockout must not lock the whole office out."""
    for _ in range(MAX_FAILURES):
        record_failure("alice", "10.0.0.1")
    assert retry_after_seconds("bob", "10.0.0.1") == 0


def test_same_user_from_another_ip_is_tracked_separately():
    for _ in range(MAX_FAILURES):
        record_failure("alice", "10.0.0.1")
    assert retry_after_seconds("alice", "10.0.0.2") == 0


def test_success_clears_the_counter():
    for _ in range(MAX_FAILURES - 1):
        record_failure("alice", "10.0.0.1")
    record_success("alice", "10.0.0.1")
    for _ in range(MAX_FAILURES - 1):
        record_failure("alice", "10.0.0.1")
    assert retry_after_seconds("alice", "10.0.0.1") == 0


def test_username_matching_is_case_insensitive():
    """Otherwise ALICE/alice/AlIcE are free extra attempts."""
    for _ in range(MAX_FAILURES):
        record_failure("Alice", "10.0.0.1")
    assert retry_after_seconds("aLiCe", "10.0.0.1") > 0


def test_window_expires(monkeypatch):
    for _ in range(MAX_FAILURES):
        record_failure("alice", "10.0.0.1")
    assert retry_after_seconds("alice", "10.0.0.1") > 0

    monkeypatch.setattr(login_throttle, "LOCKOUT_S", 0.0)
    assert retry_after_seconds("alice", "10.0.0.1") == 0


async def test_login_endpoint_throttles_after_repeated_failures(user):
    """The endpoint must actually consult the throttle, not just have one."""
    async def attempt():
        return await user.http_client.post(
            "/auth/login",
            data={"username": "nobody@example.com", "password": "wrong"},
            follow_redirects=False,
        )

    for _ in range(MAX_FAILURES):
        response = await attempt()
        assert response.headers["location"] == "/login?error=1"

    response = await attempt()
    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=throttled"


async def test_login_endpoint_does_not_throttle_a_different_user(user):
    for _ in range(MAX_FAILURES + 2):
        await user.http_client.post(
            "/auth/login",
            data={"username": "nobody@example.com", "password": "wrong"},
            follow_redirects=False,
        )

    response = await user.http_client.post(
        "/auth/login",
        data={"username": "someone-else@example.com", "password": "wrong"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/login?error=1"


# --- S3 (review 2026-10-12): spraying and the AD bind dialogs ---


def test_spraying_many_usernames_from_one_ip_is_throttled():
    """One guess per account never reaches the per-user limit."""
    for i in range(MAX_IP_FAILURES):
        record_failure(f"victim{i}", "6.6.6.6")
    assert retry_after_seconds("fresh-victim", "6.6.6.6") > 0
    assert retry_after_seconds("fresh-victim", "10.0.0.1") == 0


def test_a_success_does_not_refill_the_ip_budget():
    for i in range(MAX_IP_FAILURES):
        record_failure(f"victim{i}", "6.6.6.6")
    record_success("attacker-own-account", "6.6.6.6")
    assert retry_after_seconds("fresh-victim", "6.6.6.6") > 0


def test_ad_bind_dialog_failures_are_throttled():
    """The AD credential dialogs bind any typed account with no limit."""
    from ldap3.core.exceptions import LDAPBindError

    from not_dot_net.backend.auth.ldap import LdapConfig, LdapModifyError, _ldap_bind

    calls = []

    def wrong_password(cfg, username, password):
        calls.append(username)
        raise LDAPBindError("invalid credentials")

    cfg = LdapConfig(url="fake", domain="example.com", base_dn="dc=example,dc=com")
    for _ in range(MAX_FAILURES):
        with pytest.raises(LdapModifyError):
            _ldap_bind("Administrator", "guess", cfg, wrong_password)
    with pytest.raises(LdapModifyError, match="throttled"):
        _ldap_bind("administrator", "guess", cfg, wrong_password)
    assert len(calls) == MAX_FAILURES
