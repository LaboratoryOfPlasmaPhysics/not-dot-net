"""D2: approvers get a weekly email of what waits on them."""

from datetime import date
from unittest.mock import patch

import pytest

from not_dot_net.backend.workflow_digest import send_pending_digests
from not_dot_net.config import dashboard_config
from tests.test_next_pending import _vpn_waiting_for_approval
from tests.test_workflow_service import _create_user, _setup_roles

MONDAY, TUESDAY = date(2026, 10, 12), date(2026, 10, 13)


@pytest.fixture
def sent():
    mails = []

    async def capture(to, subject, body_html):
        if "waiting for you" in subject:  # submit notifications go through here too
            mails.append((to, subject, body_html))

    with patch("not_dot_net.backend.mail.send_mail", capture):
        yield mails


async def _director_with_two_pending():
    await _setup_roles()
    staff = await _create_user(email="staff-digest@test.com", role="staff")
    director = await _create_user(email="director-digest@test.com", role="director")
    first = await _vpn_waiting_for_approval(staff, "Alpha")
    second = await _vpn_waiting_for_approval(staff, "Bravo")
    return director, first, second


async def test_monday_digest_lists_what_waits_on_each_approver(sent):
    director, first, second = await _director_with_two_pending()
    assert await send_pending_digests(today=MONDAY) >= 1
    mine = [m for m in sent if m[0] == director.email]
    assert len(mine) == 1
    _, subject, body = mine[0]
    assert "2" in subject
    assert "Alpha" in body and "Bravo" in body
    assert f"/workflow/request/{first.id}" in body and f"/workflow/request/{second.id}" in body


async def test_nobody_without_pending_work_gets_mail(sent):
    await _director_with_two_pending()
    await send_pending_digests(today=MONDAY)
    assert "staff-digest@test.com" not in [m[0] for m in sent]


async def test_only_on_the_configured_days(sent):
    await _director_with_two_pending()
    assert await send_pending_digests(today=TUESDAY) == 0
    cfg = await dashboard_config.get()
    await dashboard_config.set(cfg.model_copy(update={"digest_weekdays": []}))
    assert await send_pending_digests(today=MONDAY) == 0
    assert sent == []


def test_digest_runs_every_morning():
    """The job checks the weekday itself, so it must be scheduled daily."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from not_dot_net.app import add_scheduled_jobs

    scheduler = AsyncIOScheduler()
    add_scheduled_jobs(scheduler)
    job = scheduler.get_job("pending_request_digest")
    assert job is not None and str(job.trigger.fields[job.trigger.FIELD_NAMES.index("day_of_week")]) == "*"
