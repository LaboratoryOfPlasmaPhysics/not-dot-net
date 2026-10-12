"""Failure throttle for POST /auth/login and the AD credential dialogs.

The LDAP fallback binds to AD with whatever username/password arrives, so an
unauthenticated caller could spray passwords at real AD accounts through the
intranet — guessing one eventually, or just tripping AD's own lockout on every
account it names, which is a denial of service against the whole lab.

Keyed on (username, IP) rather than IP alone: the lab NATs behind a handful of
addresses, so an IP-only counter would let one person's fat-fingered password
lock out everyone in the building. A second, larger per-IP budget catches
spraying (one guess per account never reaches the per-user limit); a success
does not refill it, or an attacker would interleave logins to their own account.

In-process dict, like the mail outbox worker — single replica.
"""

import time

MAX_FAILURES = 8
MAX_IP_FAILURES = 30
LOCKOUT_S = 300.0

# key -> (failure count, timestamp of the last failure). A key is
# (lowercased username, ip) per user, or (None, ip) for the IP budget.
_failures: dict[tuple[str | None, str], tuple[int, float]] = {}


def _user_key(username: str, ip: str) -> tuple[str, str]:
    return (username.strip().lower(), ip or "")


def _ip_key(ip: str) -> tuple[None, str]:
    return (None, ip or "")


def reset() -> None:
    """Drop all counters. For tests."""
    _failures.clear()


def _remaining(key, limit: int) -> int:
    count, last = _failures.get(key, (0, 0.0))
    if count < limit:
        return 0
    return max(0, int(LOCKOUT_S - (time.monotonic() - last)))


def retry_after_seconds(username: str, ip: str) -> int:
    """Seconds the caller must wait, or 0 if the attempt may proceed."""
    return max(_remaining(_user_key(username, ip), MAX_FAILURES), _remaining(_ip_key(ip), MAX_IP_FAILURES))


def _bump(key, limit: int, now: float) -> None:
    count, last = _failures.get(key, (0, now))
    # A failure after the lockout expired starts a fresh streak rather than
    # leaving the key one attempt away from locking forever.
    if count >= limit and (now - last) >= LOCKOUT_S:
        count = 0
    _failures[key] = (count + 1, now)


def record_failure(username: str, ip: str) -> None:
    now = time.monotonic()
    _bump(_user_key(username, ip), MAX_FAILURES, now)
    _bump(_ip_key(ip), MAX_IP_FAILURES, now)


def record_success(username: str, ip: str) -> None:
    _failures.pop(_user_key(username, ip), None)
