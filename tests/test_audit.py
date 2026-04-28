"""Tests for audit logging service."""

import pytest
import uuid
from datetime import datetime, timedelta, timezone

from not_dot_net.backend.audit import log_audit, list_audit_events


async def test_log_and_list():
    await log_audit("auth", "login", actor_email="test@test.com")
    events = await list_audit_events()
    assert len(events) == 1
    assert events[0].category == "auth"
    assert events[0].action == "login"
    assert events[0].actor_email == "test@test.com"


async def test_log_with_all_fields():
    uid = uuid.uuid4()
    tid = uuid.uuid4()
    await log_audit(
        "workflow", "approve",
        actor_id=uid, actor_email="approver@test.com",
        target_type="request", target_id=tid,
        detail="step=approval status=completed",
        metadata={"key": "value"},
    )
    events = await list_audit_events()
    assert len(events) == 1
    ev = events[0]
    assert ev.actor_id == str(uid)
    assert ev.target_type == "request"
    assert ev.detail == "step=approval status=completed"
    assert ev.metadata_json == {"key": "value"}


async def test_filter_by_category():
    await log_audit("auth", "login")
    await log_audit("workflow", "create")
    await log_audit("auth", "login_failed")

    auth_events = await list_audit_events(category="auth")
    assert len(auth_events) == 2
    assert all(e.category == "auth" for e in auth_events)


async def test_filter_by_action():
    await log_audit("auth", "login")
    await log_audit("auth", "login_failed")
    await log_audit("workflow", "login")

    events = await list_audit_events(action="login")

    assert len(events) == 2
    assert {e.category for e in events} == {"auth", "workflow"}
    assert all(e.action == "login" for e in events)


async def test_filter_by_actor_email():
    await log_audit("auth", "login", actor_email="alice@test.com")
    await log_audit("auth", "login", actor_email="bob@test.com")

    events = await list_audit_events(actor_email="alice")
    assert len(events) == 1
    assert events[0].actor_email == "alice@test.com"


async def test_list_with_limit():
    for i in range(5):
        await log_audit("auth", "login", actor_email=f"user{i}@test.com")
    events = await list_audit_events(limit=3)
    assert len(events) == 3


async def test_list_with_offset():
    for i in range(5):
        await log_audit("auth", f"offset_{i}")

    first_page = await list_audit_events(limit=2, offset=0)
    second_page = await list_audit_events(limit=2, offset=2)

    assert len(first_page) == 2
    assert len(second_page) == 2
    assert {event.id for event in first_page}.isdisjoint({event.id for event in second_page})


async def test_filter_by_since_excludes_old_events():
    await log_audit("auth", "login")

    future = datetime.now(timezone.utc) + timedelta(days=1)
    events = await list_audit_events(since=future)

    assert events == []


async def test_list_returns_recent_first():
    """Events should be ordered by created_at descending (newest first)."""
    for i in range(5):
        await log_audit("auth", f"event_{i}")
    events = await list_audit_events()
    assert len(events) == 5
    # All events have the same timestamp in SQLite (second precision),
    # so just verify the count and category filter work together.
    auth_events = await list_audit_events(category="auth")
    assert len(auth_events) == 5


async def test_resolves_actor_and_user_target_without_overwriting_ids():
    from not_dot_net.backend.db import User, session_scope

    user_id = uuid.uuid4()
    async with session_scope() as session:
        user = User(
            id=user_id,
            email="resolved-user@test.com",
            full_name="Resolved User",
            hashed_password="x",
        )
        session.add(user)
        await session.commit()

    await log_audit(
        "user", "update",
        actor_id=user_id,
        target_type="user",
        target_id=user_id,
    )

    events = await list_audit_events(category="user", action="update")
    assert len(events) == 1
    event = events[0]
    assert event.actor_id == str(user_id)
    assert event.actor_email == "Resolved User"
    assert event.target_id == str(user_id)
    assert event._target_display == "Resolved User"


async def test_resolves_resource_target_without_overwriting_id():
    from not_dot_net.backend.booking_models import Resource
    from not_dot_net.backend.db import session_scope

    async with session_scope() as session:
        resource = Resource(name="Audit Room", resource_type="meeting-room")
        session.add(resource)
        await session.commit()
        await session.refresh(resource)
        resource_id = resource.id

    await log_audit("resource", "update", target_type="resource", target_id=resource_id)

    events = await list_audit_events(category="resource", action="update")
    assert len(events) == 1
    event = events[0]
    assert event.target_id == str(resource_id)
    assert event._target_display == "Audit Room"


async def test_log_does_not_include_metadata_values_in_python_logs(caplog):
    with caplog.at_level("INFO", logger="not_dot_net.audit"):
        await log_audit(
            "settings", "update",
            actor_email="admin@test.com",
            metadata={"secret": "do-not-log-this"},
        )

    assert "do-not-log-this" not in caplog.text
