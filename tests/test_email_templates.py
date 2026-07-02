import pytest
from not_dot_net.backend.email_templates import (
    EmailTemplate, DEFAULT_LAYOUT, DEFAULT_TEMPLATES, EMAIL_EVENTS, _render,
    get_template, render_email, mail_templates_config, MailTemplatesConfig,
)


def test_render_tolerates_content_context_key():
    """A context key named "content" must not collide with the layout's body
    slot: the old `render(content=..., **context)` raised TypeError, which
    bypassed render_email's TemplateError fallback and crashed the send site."""
    tmpl = EmailTemplate(subject="s", body="<p>{{ workflow_label }}</p>")
    ctx = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "A",
           "workflow_label": "VPN", "content": "context-content-value"}
    _, body = _render(tmpl, DEFAULT_LAYOUT, ctx)
    assert "<p>VPN</p>" in body


def test_render_interpolates_and_wraps_in_layout():
    tmpl = EmailTemplate(subject="Hi {{ recipient_name }}", body="<p>{{ workflow_label }}</p>")
    ctx = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "Alex",
           "workflow_label": "VPN"}
    subject, body = _render(tmpl, DEFAULT_LAYOUT, ctx)
    assert subject == "Hi Alex"
    assert "<p>VPN</p>" in body          # body fragment present
    assert "LPP" in body                 # layout (header/footer) rendered around it


def test_render_autoescapes_variable_values_not_template_html():
    tmpl = EmailTemplate(subject="s", body="<p>Hello {{ recipient_name }}</p>")
    ctx = {"app_name": "LPP", "app_url": "http://x/",
           "recipient_name": "<script>alert(1)</script>", "workflow_label": "w"}
    _, body = _render(tmpl, DEFAULT_LAYOUT, ctx)
    assert "<script>alert(1)</script>" not in body            # value escaped
    assert "&lt;script&gt;" in body
    assert "<p>Hello" in body                                 # literal HTML preserved


def test_render_does_not_double_escape_body_into_layout():
    tmpl = EmailTemplate(subject="s", body="<a href='{{ app_url }}'>open</a>")
    ctx = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "A", "workflow_label": "w"}
    _, body = _render(tmpl, DEFAULT_LAYOUT, ctx)
    assert "<a href=" in body and "&lt;a" not in body         # body not escaped by layout


def test_every_event_has_a_default_template():
    for ev in EMAIL_EVENTS:
        assert ev.key in DEFAULT_TEMPLATES, f"missing default for {ev.key}"


def test_default_templates_render_with_their_sample_context():
    for ev in EMAIL_EVENTS:
        ctx = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "Sample",
               **ev.sample}
        subject, body = _render(DEFAULT_TEMPLATES[ev.key], DEFAULT_LAYOUT, ctx)
        assert subject.strip()
        assert body.strip()


BASE_CTX = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "A",
            "workflow_label": "VPN", "request_url": "http://x/workflow/request/1"}


@pytest.mark.asyncio
async def test_get_template_falls_back_to_default():
    tmpl = await get_template("approve")
    assert tmpl.subject == DEFAULT_TEMPLATES["approve"].subject


@pytest.mark.asyncio
async def test_override_wins_over_default():
    await mail_templates_config.set(MailTemplatesConfig(
        overrides={"approve": EmailTemplate(subject="Custom!", body="<p>custom</p>")}))
    tmpl = await get_template("approve")
    assert tmpl.subject == "Custom!"
    subject, body = await render_email("approve", BASE_CTX)
    assert subject == "Custom!"
    assert "<p>custom</p>" in body


@pytest.mark.asyncio
async def test_new_default_key_works_with_old_saved_config():
    # A saved config that predates an event must still render that event.
    await mail_templates_config.set(MailTemplatesConfig(overrides={}))
    subject, _ = await render_email("reject", BASE_CTX)
    assert "rejected" in subject.lower()


def test_render_does_not_html_escape_subject():
    from not_dot_net.backend.email_templates import EmailTemplate, _render, DEFAULT_LAYOUT
    tmpl = EmailTemplate(subject="[{{ app_name }}] {{ resource_name }}", body="<p>x</p>")
    ctx = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "A",
           "resource_name": "R&D <v1>"}
    subject, _ = _render(tmpl, DEFAULT_LAYOUT, ctx)
    assert subject == "[LPP] R&D <v1>"          # plain text, not &amp;/&lt;


@pytest.mark.asyncio
async def test_broken_override_falls_back_to_default(caplog):
    await mail_templates_config.set(MailTemplatesConfig(
        overrides={"approve": EmailTemplate(subject="{{ oops", body="<p>x</p>")}))  # bad Jinja
    subject, _ = await render_email("approve", BASE_CTX)
    assert subject == DEFAULT_TEMPLATES["approve"].subject.replace(
        "{{ workflow_label }}", "VPN")  # default rendered, not the broken override
    assert any("failed to render" in r.message for r in caplog.records)
