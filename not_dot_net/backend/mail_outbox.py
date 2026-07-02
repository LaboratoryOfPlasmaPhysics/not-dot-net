"""Durable mail outbox: enqueue + background worker that drains it.

`backend.mail.send_mail` writes rows here; `run_outbox_worker` (started
in `app.startup`) sends them with capped exponential backoff. Single
in-process worker; not safe across multiple replicas.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import aiosmtplib
from sqlalchemy import Index, String, Text, and_, delete, func, or_, select
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base, session_scope

logger = logging.getLogger("not_dot_net.mail_outbox")

BACKOFF = [
    timedelta(seconds=60),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(hours=24),
]
MAX_ATTEMPTS = 7
BATCH_SIZE = 50
POLL_CEILING_S = 60
SENT_RETENTION = timedelta(days=30)
PURGE_INTERVAL_S = 3600.0


class MailOutbox(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "mail_outbox"
    __table_args__ = (
        Index("ix_mail_outbox_pending", "sent_at", "failed_at", "next_attempt_at"),
        Index("ix_mail_outbox_failed_at", "failed_at"),
    )

    to_address: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(500))
    body_html: Mapped[str] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column()
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    failed_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


async def _smtp_send(to: str, subject: str, body_html: str, mail_cfg) -> None:
    """Send one mail via aiosmtplib to `to` exactly. Raises on SMTP error.

    Does NOT honor `dev_mode`/`dev_catch_all` — the caller decides both.
    """
    from not_dot_net.backend.mail import SmtpTlsMode

    msg = EmailMessage()
    msg["From"] = mail_cfg.from_address
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_html, subtype="html")
    mode = mail_cfg.smtp_tls_mode
    await aiosmtplib.send(
        msg,
        hostname=mail_cfg.smtp_host,
        port=mail_cfg.smtp_port,
        use_tls=(mode == SmtpTlsMode.SMTPS),
        start_tls=(mode == SmtpTlsMode.STARTTLS),
        username=mail_cfg.smtp_user or None,
        password=mail_cfg.smtp_password or None,
    )


async def _send_one(row: MailOutbox, mail_cfg) -> None:
    """Attempt to deliver one row. Mutates `row` in place; the caller commits.

    dev_mode without a catch-all logs instead of sending; dev_mode WITH a
    catch-all really sends — everything to the catch-all mailbox (staging).
    Production always sends to the real recipient: a leftover catch-all must
    never reroute token links or approvals.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if mail_cfg.dev_mode and not mail_cfg.dev_catch_all:
        logger.info("[MAIL dev] To: %s Subject: %s", row.to_address, row.subject)
        row.sent_at = now
        return

    to = mail_cfg.dev_catch_all if mail_cfg.dev_mode else row.to_address
    try:
        await _smtp_send(to, row.subject, row.body_html, mail_cfg)
        row.sent_at = now
    except Exception as exc:
        row.attempts += 1
        row.last_error = str(exc)[:1024]
        if row.attempts >= MAX_ATTEMPTS:
            row.failed_at = now
        else:
            row.next_attempt_at = now + BACKOFF[row.attempts - 1]


async def send_test_mail(to: str) -> None:
    """Send a synchronous test email to verify SMTP config.

    Bypasses both dev_mode and the outbox queue: the admin is verifying
    that the SMTP server is reachable RIGHT NOW with current settings,
    and wants the success/failure answer in the same click. Any SMTP
    exception propagates to the caller for display.
    """
    from not_dot_net.backend.mail import mail_config
    from not_dot_net.config import org_config

    mail_cfg = await mail_config.get()
    org_cfg = await org_config.get()
    app_name = (org_cfg.app_name or "not-dot-net").strip() or "not-dot-net"
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = f"[{app_name}] SMTP test"
    body = f"<p>Test email sent at {now_utc}.</p>"
    await _smtp_send(to, subject, body, mail_cfg)


async def _drain_outbox_once() -> int:
    """Process up to BATCH_SIZE rows whose next_attempt_at has passed.

    Returns the number of rows processed (regardless of success).
    Per-row commits so a single failure cannot roll back the others.
    """
    from not_dot_net.backend.mail import mail_config

    mail_cfg = await mail_config.get()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async with session_scope() as session:
        result = await session.execute(
            select(MailOutbox)
            .where(
                MailOutbox.sent_at.is_(None),
                MailOutbox.failed_at.is_(None),
                MailOutbox.next_attempt_at <= now,
            )
            .order_by(MailOutbox.next_attempt_at)
            .limit(BATCH_SIZE)
        )
        rows = list(result.scalars().all())

    processed = 0
    for row_id in [r.id for r in rows]:
        async with session_scope() as session:
            row = await session.get(MailOutbox, row_id)
            if row is None or row.sent_at is not None or row.failed_at is not None:
                continue
            try:
                await _send_one(row, mail_cfg)
            except Exception:
                logger.exception("Outbox drain unexpectedly raised for row %s", row_id)
            await session.commit()
            processed += 1
    return processed


async def purge_sent_rows(retention: timedelta = SENT_RETENTION) -> int:
    """Delete delivered or permanently-failed rows older than `retention`.

    Both sent and failed bodies can contain sensitive content (token links,
    codes); there is no reason to keep either once terminal. Returns rows deleted.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - retention
    async with session_scope() as session:
        result = await session.execute(
            delete(MailOutbox).where(
                or_(
                    and_(MailOutbox.sent_at.is_not(None), MailOutbox.sent_at < cutoff),
                    and_(MailOutbox.failed_at.is_not(None), MailOutbox.failed_at < cutoff),
                )
            )
        )
        await session.commit()
        return result.rowcount or 0


async def run_outbox_worker() -> None:
    """Forever: drain pending rows, sleep until the next one is due (≤ 60s)."""
    import time

    last_purge = 0.0
    while True:
        try:
            await _drain_outbox_once()
            if time.monotonic() - last_purge > PURGE_INTERVAL_S:
                purged = await purge_sent_rows()
                if purged:
                    logger.info("Purged %d delivered outbox row(s)", purged)
                last_purge = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Outbox worker iteration failed")

        try:
            sleep_s = await _seconds_until_next_attempt()
        except Exception:
            logger.exception("Failed to compute next outbox wakeup")
            sleep_s = POLL_CEILING_S
        await asyncio.sleep(sleep_s)


async def _seconds_until_next_attempt() -> float:
    """Return seconds until the soonest pending row's next_attempt_at,
    bounded above by POLL_CEILING_S. Empty queue → ceiling."""
    async with session_scope() as session:
        result = await session.execute(
            select(MailOutbox.next_attempt_at)
            .where(
                MailOutbox.sent_at.is_(None),
                MailOutbox.failed_at.is_(None),
            )
            .order_by(MailOutbox.next_attempt_at)
            .limit(1)
        )
        next_at = result.scalar_one_or_none()
    if next_at is None:
        return POLL_CEILING_S
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return max(0.0, min(POLL_CEILING_S, (next_at - now).total_seconds()))
