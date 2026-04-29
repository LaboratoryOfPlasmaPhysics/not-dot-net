"""B3: list_audit_events must not crash on non-UUID actor_id / target_id values.

actor_id is `String(36)` for historical reasons. Anything that ever writes a
non-UUID (e.g. "system", a CLI tool, a corrupted import) used to blow up the
entire audit log render with `ValueError: badly formed hexadecimal UUID string`.
"""

from not_dot_net.backend.audit import list_audit_events, log_audit


async def test_audit_log_tolerates_non_uuid_actor_id():
    """Logging an event with a non-UUID actor must not break list rendering."""
    await log_audit("auth", "login", actor_id="system")
    views = await list_audit_events(category="auth")
    assert len(views) == 1
    assert views[0].actor_id == "system"


async def test_audit_log_tolerates_non_uuid_target_id():
    await log_audit(
        "settings", "update",
        target_type="user", target_id="not-a-uuid",
    )
    views = await list_audit_events(category="settings")
    assert len(views) == 1
    # We don't crash; we display the raw id since we can't resolve it.
    assert views[0].target_id == "not-a-uuid"
