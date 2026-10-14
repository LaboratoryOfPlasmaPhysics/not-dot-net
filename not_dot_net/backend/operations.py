"""What IT should look at: failures the app can't fix on its own."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from not_dot_net.backend.ad_account_config import ad_account_config
from not_dot_net.backend.audit import AuditEvent
from not_dot_net.backend.db import session_scope
from not_dot_net.backend.effect_retry import FailedEffect, pending_effects
from not_dot_net.backend.mail import mail_config
from not_dot_net.backend.mail_outbox import MailOutbox

# Retries start after a minute; a mail still queued an hour later is stuck.
STUCK_MAIL_AFTER = timedelta(hours=1)


@dataclass(frozen=True)
class OperationsStatus:
    failed_effects: list[FailedEffect] = field(default_factory=list)
    failed_mails: int = 0
    stuck_mails: int = 0
    last_ad_sync: AuditEvent | None = None
    warnings: list[str] = field(default_factory=list)  # i18n keys

    @property
    def problems(self) -> int:
        return len(self.failed_effects) + self.failed_mails + self.stuck_mails + len(self.warnings)


async def operations_status(*, app_dev_mode: bool) -> OperationsStatus:
    failed_mails, stuck_mails = await _mail_counts()
    return OperationsStatus(
        failed_effects=await pending_effects(),
        failed_mails=failed_mails,
        stuck_mails=stuck_mails,
        last_ad_sync=await _last_ad_sync(),
        warnings=await _config_warnings(app_dev_mode),
    )


async def _mail_counts() -> tuple[int, int]:
    stuck_before = datetime.now(timezone.utc).replace(tzinfo=None) - STUCK_MAIL_AFTER
    pending = (MailOutbox.sent_at.is_(None)) & (MailOutbox.failed_at.is_(None))
    async with session_scope() as session:
        failed = await session.scalar(select(func.count()).where(MailOutbox.failed_at.is_not(None)))
        stuck = await session.scalar(
            select(func.count()).where(pending, MailOutbox.created_at < stuck_before)
        )
    return failed or 0, stuck or 0


async def _last_ad_sync() -> AuditEvent | None:
    async with session_scope() as session:
        return await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.category == "settings", AuditEvent.action == "ldap_sync")
            .order_by(AuditEvent.created_at.desc())
            .limit(1)
        )


async def _config_warnings(app_dev_mode: bool) -> list[str]:
    warnings = []
    if not (await ad_account_config.get()).users_ous:
        warnings.append("ops_warn_no_user_ou")
    if (await mail_config.get()).dev_mode and not app_dev_mode:
        warnings.append("ops_warn_mail_not_sent")
    return warnings
