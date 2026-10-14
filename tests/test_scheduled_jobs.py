"""Daily maintenance jobs must use cron triggers (interval triggers reset on
every pod restart and can starve the jobs forever) and must include the
encrypted-file retention purge."""

from datetime import datetime, timedelta, timezone


async def test_scheduled_jobs_use_cron_and_include_retention_purge():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    from not_dot_net.app import add_scheduled_jobs

    scheduler = AsyncIOScheduler()
    add_scheduled_jobs(scheduler)
    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert set(jobs) == {"booking_end_reminders", "pending_request_digest", "retention_purge"}
    for job in jobs.values():
        assert isinstance(job.trigger, CronTrigger), f"{job.id} must use a cron trigger"


async def test_retention_purge_job_deletes_expired_files():
    from sqlalchemy import select

    from not_dot_net.backend.db import session_scope
    from not_dot_net.backend.encrypted_storage import (
        EncryptedFile, run_retention_purge_job, store_encrypted,
    )

    enc = await store_encrypted(b"secret-doc", "id.pdf", "application/pdf", uploaded_by=None)
    async with session_scope() as session:
        row = await session.get(EncryptedFile, enc.id)
        row.retained_until = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        await session.commit()

    await run_retention_purge_job()

    async with session_scope() as session:
        result = await session.execute(select(EncryptedFile).where(EncryptedFile.id == enc.id))
        assert result.scalar_one_or_none() is None
