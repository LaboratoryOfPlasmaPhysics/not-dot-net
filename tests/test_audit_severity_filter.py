"""The severity filter finds events older than the newest page (review 2026-10-12 F7).

The view loaded the 500 newest events, then filtered by severity in Python:
a critical event behind 500 routine logins was invisible, with no notice.
"""

from not_dot_net.backend.audit import log_audit
from not_dot_net.frontend.audit_log import AUDIT_PAGE, events_of_severity


async def test_critical_event_behind_a_full_page_is_found():
    await log_audit("settings", "update_role", detail="old but critical")
    for _ in range(AUDIT_PAGE + 5):
        await log_audit("auth", "login", metadata={"success": True})

    found, truncated = await events_of_severity("red")

    assert [ev.detail for ev, _ in found] == ["old but critical"]
    assert truncated is False


async def test_more_matches_than_a_page_reports_truncation():
    for _ in range(AUDIT_PAGE + 1):
        await log_audit("auth", "login", metadata={"success": True})

    found, truncated = await events_of_severity("green")

    assert len(found) == AUDIT_PAGE
    assert truncated is True
