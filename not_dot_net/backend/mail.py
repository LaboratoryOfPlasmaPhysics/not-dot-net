"""Mail config + the public enqueue API.

`send_mail` is the only public entrypoint. It writes a row to the
`mail_outbox` table; the background worker in `mail_outbox.py` drains
that table and performs the actual SMTP send using the current
`MailConfig`.
"""

import logging
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from not_dot_net.backend.app_config import section
from not_dot_net.backend.db import session_scope

logger = logging.getLogger("not_dot_net.mail")


class SmtpTlsMode(str, Enum):
    NONE = "none"
    STARTTLS = "starttls"      # connect plaintext, upgrade with STARTTLS (typical port 587)
    SMTPS = "smtps"            # TLS on connect (typical port 465)


class MailConfig(BaseModel):
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_tls_mode: SmtpTlsMode = SmtpTlsMode.NONE
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = "noreply@not-dot-net.dev"
    dev_mode: bool = Field(
        True, description="Log emails instead of sending them. Turn OFF in production."
    )
    dev_catch_all: str = Field(
        "", description="Staging mailbox receiving ALL mail while dev mode is on. Ignored in production."
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_smtp_tls(cls, data):
        """Translate the pre-2026-05-07 `smtp_tls: bool` field to
        `smtp_tls_mode` so existing config rows keep working without
        admin intervention. Old True → starttls (the only mode the old
        bool encoded); old False → none."""
        if not isinstance(data, dict):
            return data
        if "smtp_tls" in data and "smtp_tls_mode" not in data:
            data["smtp_tls_mode"] = (
                SmtpTlsMode.STARTTLS.value if data.pop("smtp_tls") else SmtpTlsMode.NONE.value
            )
        return data


mail_config = section("mail", MailConfig, label="Email / SMTP")


async def send_mail(to: str, subject: str, body_html: str) -> None:
    """Enqueue an outbound mail. Returns when the row is committed.

    The worker (run_outbox_worker) drains the table and performs the
    actual SMTP send using the current MailConfig.
    """
    from not_dot_net.backend.mail_outbox import MailOutbox

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_scope() as session:
        row = MailOutbox(
            to_address=to,
            subject=subject,
            body_html=body_html,
            next_attempt_at=now,
        )
        session.add(row)
        await session.commit()
