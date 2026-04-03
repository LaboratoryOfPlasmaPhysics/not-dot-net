"""Async mail sending with dev-mode logging."""

import logging
from email.message import EmailMessage

import aiosmtplib
from pydantic import BaseModel

from not_dot_net.backend.app_config import section

logger = logging.getLogger("not_dot_net.mail")


class MailConfig(BaseModel):
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_tls: bool = False
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = "noreply@not-dot-net.dev"
    dev_mode: bool = True
    dev_catch_all: str = ""


mail_config = section("mail", MailConfig, label="Email / SMTP")


async def send_mail(
    to: str,
    subject: str,
    body_html: str,
    mail_settings: MailConfig,
) -> None:
    effective_to = to
    if mail_settings.dev_catch_all:
        effective_to = mail_settings.dev_catch_all

    if mail_settings.dev_mode:
        logger.info("[MAIL dev] To: %s (original: %s) Subject: %s", effective_to, to, subject)
        return

    msg = EmailMessage()
    msg["From"] = mail_settings.from_address
    msg["To"] = effective_to
    msg["Subject"] = subject
    msg.set_content(body_html, subtype="html")

    await aiosmtplib.send(
        msg,
        hostname=mail_settings.smtp_host,
        port=mail_settings.smtp_port,
        start_tls=mail_settings.smtp_tls,
        username=mail_settings.smtp_user or None,
        password=mail_settings.smtp_password or None,
    )
