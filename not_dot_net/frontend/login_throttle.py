"""Failure throttle for POST /auth/login.

The LDAP fallback binds to AD with whatever username/password arrives, so an
unauthenticated caller could spray passwords at real AD accounts through the
intranet — guessing one eventually, or just tripping AD's own lockout on every
account it names, which is a denial of service against the whole lab.

Keyed on (username, IP) rather than IP alone: the lab NATs behind a handful of
addresses, so an IP-only counter would let one person's fat-fingered password
lock out everyone in the building.

In-process dict, like the mail outbox worker — single replica.
"""

import time

MAX_FAILURES = 8
LOCKOUT_S = 300.0

# (lowercased username, ip) -> (failure count, timestamp of the last failure)
_failures: dict[tuple[str, str], tuple[int, float]] = {}


def _key(username: str, ip: str) -> tuple[str, str]:
    return (username.strip().lower(), ip or "")


def reset() -> None:
    """Drop all counters. For tests."""
    _failures.clear()


def retry_after_seconds(username: str, ip: str) -> int:
    """Seconds the caller must wait, or 0 if the attempt may proceed."""
    entry = _failures.get(_key(username, ip))
    if entry is None:
        return 0
    count, last = entry
    if count < MAX_FAILURES:
        return 0
    remaining = LOCKOUT_S - (time.monotonic() - last)
    return max(0, int(remaining)) if remaining > 0 else 0


def record_failure(username: str, ip: str) -> None:
    key = _key(username, ip)
    now = time.monotonic()
    count, last = _failures.get(key, (0, now))
    # A failure after the lockout expired starts a fresh streak rather than
    # leaving the account one attempt away from locking forever.
    if count >= MAX_FAILURES and (now - last) >= LOCKOUT_S:
        count = 0
    _failures[key] = (count + 1, now)


def record_success(username: str, ip: str) -> None:
    _failures.pop(_key(username, ip), None)
