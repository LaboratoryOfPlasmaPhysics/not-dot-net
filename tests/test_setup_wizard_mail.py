"""D1 — a fresh production deploy must not silently swallow every email.

MailConfig.dev_mode defaults to True and ConfigSection.get() returns schema
defaults when no row exists, so an instance where nobody opened Settings ->
Email marks every outbox row sent while only logging it: token links,
verification codes and booking reminders vanish without error.

The default itself is deliberate (safe-by-default: better to blackhole than to
spam real users from a misconfigured instance), so the wizard asks instead.
"""
import pytest

from not_dot_net.backend.mail import mail_config
from not_dot_net.frontend.setup_wizard import complete_setup


async def test_configuring_mail_turns_dev_mode_off():
    ok = await complete_setup(
        "boss@example.com", "pw",
        smtp_host="smtp.example.com", smtp_port=25, from_address="noreply@example.com",
    )
    assert ok

    cfg = await mail_config.get()
    assert cfg.dev_mode is False, "mail still blackholed after the admin configured SMTP"
    assert cfg.smtp_host == "smtp.example.com"
    assert cfg.smtp_port == 25
    assert cfg.from_address == "noreply@example.com"


async def test_skipping_mail_leaves_the_safe_default():
    ok = await complete_setup("boss2@example.com", "pw")
    assert ok

    cfg = await mail_config.get()
    assert cfg.dev_mode is True, "dev_mode flipped without the admin configuring SMTP"


async def test_blank_smtp_host_counts_as_skipping():
    ok = await complete_setup("boss3@example.com", "pw", smtp_host="   ")
    assert ok
    assert (await mail_config.get()).dev_mode is True


async def test_second_setup_still_refused():
    """The stale-tab guard must survive the new parameters."""
    assert await complete_setup("first@example.com", "pw") is True
    assert await complete_setup("second@example.com", "pw") is False
