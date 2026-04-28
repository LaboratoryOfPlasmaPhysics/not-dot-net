import pytest
from unittest.mock import AsyncMock, patch
from not_dot_net.backend.mail import send_mail
from not_dot_net.backend.mail import MailConfig


async def test_dev_mode_logs_to_console(caplog):
    settings = MailConfig(dev_mode=True)
    with caplog.at_level("INFO", logger="not_dot_net.mail"):
        await send_mail(
            to="user@example.com",
            subject="Test Subject",
            body_html="<p>Hello</p>",
            mail_settings=settings,
        )
    assert "user@example.com" in caplog.text
    assert "Test Subject" in caplog.text


async def test_dev_mode_does_not_call_smtp():
    settings = MailConfig(dev_mode=True)
    with patch("not_dot_net.backend.mail.aiosmtplib") as mock_smtp:
        mock_smtp.send = AsyncMock()
        await send_mail(
            to="user@example.com",
            subject="Dev Test",
            body_html="<p>Hello</p>",
            mail_settings=settings,
        )
        mock_smtp.send.assert_not_called()


async def test_dev_catch_all_redirects(caplog):
    settings = MailConfig(dev_mode=True, dev_catch_all="catch@example.com")
    with caplog.at_level("INFO", logger="not_dot_net.mail"):
        await send_mail(
            to="real@example.com",
            subject="Test",
            body_html="<p>Hi</p>",
            mail_settings=settings,
        )
    assert "catch@example.com" in caplog.text
    assert "real@example.com" in caplog.text  # original still mentioned


async def test_dev_mode_does_not_log_body_or_token_values(caplog):
    settings = MailConfig(dev_mode=True, smtp_password="smtp-secret")
    token_link = "http://localhost/workflow/token/sensitive-token"
    with caplog.at_level("INFO", logger="not_dot_net.mail"):
        await send_mail(
            to="user@example.com",
            subject="Token Email",
            body_html=f"<p>Use {token_link}</p>",
            mail_settings=settings,
        )

    assert "Token Email" in caplog.text
    assert token_link not in caplog.text
    assert "sensitive-token" not in caplog.text
    assert "smtp-secret" not in caplog.text


async def test_production_mode_calls_aiosmtplib():
    settings = MailConfig(
        dev_mode=False,
        smtp_host="smtp.test.com",
        smtp_port=587,
        smtp_tls=True,
        from_address="noreply@test.com",
    )
    with patch("not_dot_net.backend.mail.aiosmtplib") as mock_smtp:
        mock_smtp.send = AsyncMock()
        await send_mail(
            to="user@example.com",
            subject="Prod Test",
            body_html="<p>Content</p>",
            mail_settings=settings,
        )
        mock_smtp.send.assert_called_once()
        msg = mock_smtp.send.call_args[0][0]
        assert msg["To"] == "user@example.com"
        assert msg["Subject"] == "Prod Test"
        assert msg["From"] == "noreply@test.com"


async def test_production_passes_smtp_settings_to_aiosmtplib():
    settings = MailConfig(
        dev_mode=False,
        smtp_host="smtp.secure.test",
        smtp_port=465,
        smtp_tls=True,
        smtp_user="smtp-user",
        smtp_password="smtp-password",
        from_address="noreply@test.com",
    )
    with patch("not_dot_net.backend.mail.aiosmtplib") as mock_smtp:
        mock_smtp.send = AsyncMock()
        await send_mail(
            to="user@example.com",
            subject="Prod Test",
            body_html="<p>Content</p>",
            mail_settings=settings,
        )

    _, kwargs = mock_smtp.send.call_args
    assert kwargs == {
        "hostname": "smtp.secure.test",
        "port": 465,
        "start_tls": True,
        "username": "smtp-user",
        "password": "smtp-password",
    }


async def test_production_omits_empty_smtp_credentials():
    settings = MailConfig(
        dev_mode=False,
        smtp_host="smtp.test.com",
        smtp_user="",
        smtp_password="",
    )
    with patch("not_dot_net.backend.mail.aiosmtplib") as mock_smtp:
        mock_smtp.send = AsyncMock()
        await send_mail(
            to="user@example.com",
            subject="No Auth",
            body_html="<p>Content</p>",
            mail_settings=settings,
        )

    _, kwargs = mock_smtp.send.call_args
    assert kwargs["username"] is None
    assert kwargs["password"] is None


async def test_production_message_contains_html_body():
    settings = MailConfig(dev_mode=False, smtp_host="smtp.test.com")
    with patch("not_dot_net.backend.mail.aiosmtplib") as mock_smtp:
        mock_smtp.send = AsyncMock()
        await send_mail(
            to="user@example.com",
            subject="HTML",
            body_html="<h1>Hello</h1>",
            mail_settings=settings,
        )

    msg = mock_smtp.send.call_args[0][0]
    assert msg.get_content_subtype() == "html"
    assert "<h1>Hello</h1>" in msg.get_content()


async def test_production_with_catch_all_redirects():
    settings = MailConfig(
        dev_mode=False,
        smtp_host="smtp.test.com",
        dev_catch_all="catch@example.com",
        from_address="noreply@test.com",
    )
    with patch("not_dot_net.backend.mail.aiosmtplib") as mock_smtp:
        mock_smtp.send = AsyncMock()
        await send_mail(
            to="real@example.com",
            subject="Test",
            body_html="<p>Hi</p>",
            mail_settings=settings,
        )
        msg = mock_smtp.send.call_args[0][0]
        assert msg["To"] == "catch@example.com"
