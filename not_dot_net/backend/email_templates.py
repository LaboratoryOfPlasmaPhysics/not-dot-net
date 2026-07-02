"""Configurable email templates: code-level defaults + DB overrides, rendered
with a sandboxed Jinja environment. `render_email(key, context)` is the single
entry point used by notifications, workflow and booking send sites."""

import logging
from dataclasses import dataclass

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup
from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import section

logger = logging.getLogger("not_dot_net.email_templates")

COMMON_VARIABLES = ["app_name", "app_url", "recipient_name"]


class EmailTemplate(BaseModel):
    subject: str
    body: str


@dataclass(frozen=True)
class EmailEvent:
    key: str
    group: str
    label: str
    variables: list[str]
    sample: dict


DEFAULT_LAYOUT = """\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;
            color:#222;font-size:14px;line-height:1.5">
  <div style="background:#0F52AC;color:#fff;padding:12px 16px;font-size:16px">
    {{ app_name }}
  </div>
  <div style="padding:16px">
    {{ content }}
  </div>
  <div style="padding:12px 16px;color:#888;font-size:12px;border-top:1px solid #eee">
    <a href="{{ app_url }}" style="color:#0F52AC">{{ app_name }}</a> —
    this is an automated message, please do not reply.
  </div>
</div>"""


def _t(subject: str, body: str) -> EmailTemplate:
    return EmailTemplate(subject=subject, body=body)


DEFAULT_TEMPLATES: dict[str, EmailTemplate] = {
    "submit": _t(
        "A new {{ workflow_label }} request needs your attention",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>A new <strong>{{ workflow_label }}</strong> request"
        "{% if requester_name %} from {{ requester_name }}{% endif %}"
        " has been submitted and requires your action.</p>"
        "<p><a href=\"{{ request_url }}\">Review the request</a></p>",
    ),
    "step_assigned": _t(
        "Action required: {{ step_label }} for {{ workflow_label }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>You have a pending action on <strong>{{ workflow_label }}</strong>: "
        "{{ step_label }}.</p>"
        "<p><a href=\"{{ request_url }}\">Open the request</a></p>",
    ),
    "approve": _t(
        "Your {{ workflow_label }} request has been approved",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Your <strong>{{ workflow_label }}</strong> request has been approved.</p>"
        "<p><a href=\"{{ request_url }}\">View your request</a></p>",
    ),
    "reject": _t(
        "Your {{ workflow_label }} request was rejected",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Your <strong>{{ workflow_label }}</strong> request was rejected.</p>"
        "<p><a href=\"{{ request_url }}\">View your request</a></p>",
    ),
    "token_link": _t(
        "Please complete your information for {{ workflow_label }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Please complete your information for <strong>{{ workflow_label }}</strong> "
        "by visiting the link below:</p>"
        "<p><a href=\"{{ token_url }}\">{{ token_url }}</a></p>",
    ),
    "corrections_with_link": _t(
        "Corrections needed for your {{ workflow_label }} submission",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>The administration team has requested corrections on your "
        "<strong>{{ workflow_label }}</strong> submission.</p>"
        "<p>Please update your information here:</p>"
        "<p><a href=\"{{ token_url }}\">{{ token_url }}</a></p>",
    ),
    "request_corrections": _t(
        "Corrections needed for your {{ workflow_label }} submission",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>The administration team has requested corrections on your "
        "<strong>{{ workflow_label }}</strong> submission.</p>"
        "<p>Please visit the link you received previously to update your information.</p>",
    ),
    "complete": _t(
        "Your {{ workflow_label }} is complete — welcome!",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Your <strong>{{ workflow_label }}</strong> onboarding is now complete. "
        "Your account has been created.</p>"
        "<p><a href=\"{{ app_url }}\">Sign in to the intranet</a></p>",
    ),
    # The initial password is deliberately NOT in this email.
    "account_created": _t(
        "{{ workflow_label }}: your account is ready",
        "<p>Hello {{ display_name }},</p>"
        "<p>Your account has been created:</p>"
        "<ul>"
        "<li><strong>Login:</strong> {{ sam }}</li>"
        "<li><strong>Email:</strong> {{ mail }}</li>"
        "</ul>"
        "<p>Your administrator will give you your initial password; you will be "
        "asked to change it on first login.</p>"
        "<p><a href=\"{{ app_url }}\">Sign in to the intranet</a></p>",
    ),
    "booking_reminder": _t(
        "[{{ app_name }}] Your booking ends {{ delay_label }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Your booking is ending soon.</p>"
        "<table>"
        "<tr><td><strong>Resource</strong></td><td>{{ resource_name }}</td></tr>"
        "<tr><td><strong>Start date</strong></td><td>{{ start_date }}</td></tr>"
        "<tr><td><strong>End date</strong></td><td>{{ end_date }}</td></tr>"
        "<tr><td><strong>OS</strong></td><td>{{ os }}</td></tr>"
        "<tr><td><strong>Software</strong></td><td>{{ software }}</td></tr>"
        "</table>"
        "<p>Please prepare to return or release the resource at the end of your booking.</p>"
        "<p><a href=\"{{ bookings_url }}\">View your bookings</a></p>",
    ),
    "resource_status": _t(
        "[{{ app_name }}] {{ subject_line }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>{{ headline }}</p>"
        "<table>"
        "<tr><td><strong>Resource</strong></td><td>{{ resource_name }}</td></tr>"
        "<tr><td><strong>Location</strong></td><td>{{ location }}</td></tr>"
        "</table>"
        "<p><a href=\"{{ bookings_url }}\">View your bookings</a></p>",
    ),
    "booking_migrated": _t(
        "[{{ app_name }}] Your booking was moved to {{ new_resource_name }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Your booking has been moved to another resource.</p>"
        "<table>"
        "<tr><td><strong>Previous resource</strong></td><td>{{ old_resource_name }}</td></tr>"
        "<tr><td><strong>New resource</strong></td><td>{{ new_resource_name }}</td></tr>"
        "<tr><td><strong>Location</strong></td><td>{{ location }}</td></tr>"
        "<tr><td><strong>Start date</strong></td><td>{{ start_date }}</td></tr>"
        "<tr><td><strong>Last usage day</strong></td><td>{{ last_day }}</td></tr>"
        "</table>"
        "<p><a href=\"{{ bookings_url }}\">View your bookings</a></p>",
    ),
    "resource_out_of_service": _t(
        "[{{ app_name }}] Resource out of service: {{ resource_name }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>{{ headline }}</p>"
        "<table>"
        "<tr><td><strong>Resource</strong></td><td>{{ resource_name }}</td></tr>"
        "<tr><td><strong>Location</strong></td><td>{{ location }}</td></tr>"
        "</table>"
        "<p><a href=\"{{ bookings_url }}\">View bookings</a></p>",
    ),
}


EMAIL_EVENTS: list[EmailEvent] = [
    EmailEvent("submit", "Workflow", "New request submitted",
               ["workflow_label", "requester_name", "request_url"],
               {"workflow_label": "VPN Access", "requester_name": "Marie Curie",
                "request_url": "http://x/workflow/request/123"}),
    EmailEvent("step_assigned", "Workflow", "Step assigned",
               ["workflow_label", "step_label", "request_url"],
               {"workflow_label": "VPN Access", "step_label": "Manager approval",
                "request_url": "http://x/workflow/request/123"}),
    EmailEvent("approve", "Workflow", "Request approved",
               ["workflow_label", "request_url"],
               {"workflow_label": "VPN Access", "request_url": "http://x/workflow/request/123"}),
    EmailEvent("reject", "Workflow", "Request rejected",
               ["workflow_label", "request_url"],
               {"workflow_label": "VPN Access", "request_url": "http://x/workflow/request/123"}),
    EmailEvent("token_link", "Workflow", "Complete your information",
               ["workflow_label", "token_url"],
               {"workflow_label": "Onboarding", "token_url": "http://x/workflow/token/abc"}),
    EmailEvent("corrections_with_link", "Workflow", "Corrections needed (with link)",
               ["workflow_label", "token_url"],
               {"workflow_label": "Onboarding", "token_url": "http://x/workflow/token/abc"}),
    EmailEvent("request_corrections", "Workflow", "Corrections needed",
               ["workflow_label"],
               {"workflow_label": "Onboarding"}),
    EmailEvent("complete", "Workflow", "Onboarding complete",
               ["workflow_label", "app_url"],
               {"workflow_label": "Onboarding"}),
    EmailEvent("account_created", "Workflow", "Account created",
               ["workflow_label", "display_name", "sam", "mail", "app_url"],
               {"workflow_label": "Onboarding", "display_name": "Marie Curie",
                "sam": "curiem", "mail": "marie.curie@lpp.fr"}),
    EmailEvent("booking_reminder", "Booking", "Booking ending soon",
               ["delay_label", "resource_name", "start_date", "end_date", "os",
                "software", "bookings_url"],
               {"delay_label": "in 3 days", "resource_name": "Laptop-07",
                "start_date": "2026-07-01", "end_date": "2026-07-10",
                "os": "Ubuntu 24.04", "software": "Python, MATLAB",
                "bookings_url": "http://x/?tab=bookings"}),
    EmailEvent("resource_status", "Booking", "Resource status change",
               ["subject_line", "headline", "resource_name", "location", "bookings_url"],
               {"subject_line": "Your resource is ready for pickup",
                "headline": "Your booked resource is ready for pickup.",
                "resource_name": "Laptop-07", "location": "Room 12",
                "bookings_url": "http://x/?tab=bookings"}),
    EmailEvent("booking_migrated", "Booking", "Booking migrated to another resource",
               ["old_resource_name", "new_resource_name", "location", "start_date",
                "last_day", "bookings_url"],
               {"old_resource_name": "Laptop-07", "new_resource_name": "Laptop-09",
                "location": "Room 12", "start_date": "2026-07-10",
                "last_day": "2026-07-14", "bookings_url": "http://x/?tab=bookings"}),
    EmailEvent("resource_out_of_service", "Booking", "Resource out of service",
               ["headline", "resource_name", "location", "bookings_url"],
               {"headline": "Laptop-07 has been marked out of service.",
                "resource_name": "Laptop-07", "location": "Room 12",
                "bookings_url": "http://x/?tab=bookings"}),
]


class MailTemplatesConfig(BaseModel):
    layout: str = DEFAULT_LAYOUT
    overrides: dict[str, EmailTemplate] = Field(default_factory=dict)


mail_templates_config = section("mail_templates", MailTemplatesConfig, label="Email Templates")

_env = SandboxedEnvironment(autoescape=True)
_subject_env = SandboxedEnvironment(autoescape=False)


def _render(template: EmailTemplate, layout: str, context: dict) -> tuple[str, str]:
    subject = _subject_env.from_string(template.subject).render(**context)
    inner = _env.from_string(template.body).render(**context)
    # Merge instead of a `content=` kwarg: a context key named "content" would
    # raise TypeError, which bypasses render_email's TemplateError fallback.
    body_html = _env.from_string(layout).render({**context, "content": Markup(inner)})
    return subject, body_html


async def get_template(key: str) -> EmailTemplate:
    cfg = await mail_templates_config.get()
    return cfg.overrides.get(key) or DEFAULT_TEMPLATES[key]


async def render_email(key: str, context: dict) -> tuple[str, str]:
    cfg = await mail_templates_config.get()
    template = cfg.overrides.get(key) or DEFAULT_TEMPLATES[key]
    layout = cfg.layout or DEFAULT_LAYOUT
    try:
        return _render(template, layout, context)
    except TemplateError:
        logger.warning("Email template %r failed to render; using default", key,
                       exc_info=True)
        return _render(DEFAULT_TEMPLATES[key], DEFAULT_LAYOUT, context)
