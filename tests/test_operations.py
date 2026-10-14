"""I1: one place telling IT what is broken."""

from datetime import datetime, timedelta, timezone

from not_dot_net.backend.ad_account_config import ad_account_config
from not_dot_net.backend.audit import log_audit
from not_dot_net.backend.db import session_scope
from not_dot_net.backend.effect_retry import enqueue_failed_effect
from not_dot_net.backend.mail import mail_config
from not_dot_net.backend.mail_outbox import MailOutbox
from not_dot_net.backend.operations import operations_status
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.workflow_service import create_request


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _mail(*, age: timedelta, failed: bool = False, sent: bool = False):
    created = _now() - age
    async with session_scope() as session:
        session.add(MailOutbox(
            to_address="x@y", subject="s", body_html="b", next_attempt_at=created,
            created_at=created, failed_at=created if failed else None, sent_at=created if sent else None,
        ))
        await session.commit()


async def _configure(*, users_ous: list[str], mail_dev_mode: bool):
    ad = await ad_account_config.get()
    await ad_account_config.set(ad.model_copy(update={"users_ous": users_ous}))
    mail = await mail_config.get()
    await mail_config.set(mail.model_copy(update={"dev_mode": mail_dev_mode}))


async def test_all_clear():
    await _configure(users_ous=["OU=Users,DC=lpp"], mail_dev_mode=False)
    status = await operations_status(app_dev_mode=False)
    assert status.problems == 0


async def test_failed_ad_steps_are_listed_with_their_request():
    await _configure(users_ous=["OU=Users,DC=lpp"], mail_dev_mode=False)
    from tests.test_workflow_service import _create_user

    requester = await _create_user(email="ops-req@test.com")
    req = await create_request(
        workflow_type="vpn_access", created_by=requester.id,
        data={"target_name": "Anna", "target_email": "anna@x"}, actor=SYSTEM_ACTOR,
    )
    await enqueue_failed_effect(
        request_id=req.id, step_key="approval", action="approve",
        kind="ad_add_to_groups", params={}, error="target has no ldap_dn",
    )
    status = await operations_status(app_dev_mode=False)
    assert [e.request_id for e in status.failed_effects] == [req.id]
    assert status.problems == 1


async def test_mail_counts_ignore_sent_and_fresh_mail():
    await _configure(users_ous=["OU=Users,DC=lpp"], mail_dev_mode=False)
    await _mail(age=timedelta(days=1), failed=True)
    await _mail(age=timedelta(hours=3))            # pending for 3 h: stuck
    await _mail(age=timedelta(minutes=5))          # pending, just queued: fine
    await _mail(age=timedelta(days=1), sent=True)  # delivered: fine
    status = await operations_status(app_dev_mode=False)
    assert (status.failed_mails, status.stuck_mails) == (1, 1)
    assert status.problems == 2


async def test_config_warnings():
    await _configure(users_ous=[], mail_dev_mode=True)
    status = await operations_status(app_dev_mode=False)
    assert set(status.warnings) == {"ops_warn_no_user_ou", "ops_warn_mail_not_sent"}


async def test_mail_log_only_is_fine_in_dev():
    await _configure(users_ous=["OU=Users,DC=lpp"], mail_dev_mode=True)
    assert (await operations_status(app_dev_mode=True)).warnings == []


async def test_last_ad_sync_comes_from_the_audit_log():
    await _configure(users_ous=["OU=Users,DC=lpp"], mail_dev_mode=False)
    assert (await operations_status(app_dev_mode=False)).last_ad_sync is None
    await log_audit("settings", "ldap_sync", detail="created=2 updated=5")
    sync = (await operations_status(app_dev_mode=False)).last_ad_sync
    assert sync is not None and sync.detail == "created=2 updated=5"
