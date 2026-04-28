"""Event-driven notification engine for workflow transitions."""

from not_dot_net.config import WorkflowConfig, NotificationRuleConfig


# --- Email Templates ---

TEMPLATES = {
    "submit": {
        "subject": "A new {workflow_label} request needs your attention",
        "body": "<p>A new <strong>{workflow_label}</strong> request has been submitted"
                " and requires your action.</p>",
    },
    "approve": {
        "subject": "Your {workflow_label} request has been approved",
        "body": "<p>Your <strong>{workflow_label}</strong> request has been approved.</p>",
    },
    "reject": {
        "subject": "Your {workflow_label} request was rejected",
        "body": "<p>Your <strong>{workflow_label}</strong> request was rejected.</p>",
    },
    "step_assigned": {
        "subject": "Action required: {step_label} for {workflow_label}",
        "body": "<p>You have a pending action on <strong>{workflow_label}</strong>: "
                "{step_label}.</p>",
    },
    "token_link": {
        "subject": "Please complete your information for {workflow_label}",
        "body": "<p>Please complete your information by visiting the link below:</p>"
                '<p><a href="{link}">{link}</a></p>',
    },
    "request_corrections": {
        "subject": "Corrections needed for your {workflow_label} submission",
        "body": "<p>The administration team has requested corrections on your "
                "<strong>{workflow_label}</strong> submission.</p>"
                "<p>Please visit the link you received previously to update your information.</p>",
    },
    "corrections_with_link": {
        "subject": "Corrections needed for your {workflow_label} submission",
        "body": "<p>The administration team has requested corrections on your "
                "<strong>{workflow_label}</strong> submission.</p>"
                '<p>Please visit the following link to update your information:</p>'
                '<p><a href="{link}">{link}</a></p>',
    },
    "complete": {
        "subject": "Your {workflow_label} is complete — welcome!",
        "body": "<p>Your <strong>{workflow_label}</strong> onboarding is now complete. "
                "Your account has been created.</p>",
    },
}


def render_email(event: str, workflow_label: str, **kwargs) -> tuple[str, str]:
    """Render an email template. Returns (subject, body_html)."""
    template = TEMPLATES.get(event)
    if template is None:
        raise ValueError(f"No email template for event: {event}")
    subject = template["subject"].format(workflow_label=workflow_label, **kwargs)
    body = template["body"].format(workflow_label=workflow_label, **kwargs)
    return subject, body


def _matching_rules(
    workflow: WorkflowConfig, event: str, step_key: str
) -> list[NotificationRuleConfig]:
    """Find notification rules that match this event + step."""
    matched = []
    for rule in workflow.notifications:
        if rule.event != event:
            continue
        if rule.step is not None and rule.step != step_key:
            continue
        matched.append(rule)
    return matched


async def resolve_recipients(
    notify_targets: list[str],
    request,
    get_user_email,
    get_users_by_role,
    get_users_by_permission=None,
) -> list[str]:
    """Resolve notification targets to email addresses."""
    emails = set()
    for target in notify_targets:
        if target == "requester" and request.created_by:
            email = await get_user_email(request.created_by)
            if email:
                emails.add(email)
        elif target == "target_person" and request.target_email:
            emails.add(request.target_email)
        elif target.startswith("permission:"):
            if get_users_by_permission is None:
                continue
            perm = target.split(":", 1)[1]
            users = await get_users_by_permission(perm)
            for user in users:
                emails.add(user.email)
        else:
            users = await get_users_by_role(target)
            for user in users:
                emails.add(user.email)
    return list(emails)


async def notify(
    request,
    event: str,
    step_key: str,
    workflow: WorkflowConfig,
    mail_settings,
    get_user_email,
    get_users_by_role,
    get_users_by_permission=None,
) -> list[str]:
    """Fire notifications for a workflow event. Returns list of emails sent to."""
    from not_dot_net.backend.mail import send_mail
    from not_dot_net.config import org_config

    org_cfg = await org_config.get()
    base_url = org_cfg.base_url.rstrip("/")

    rules = _matching_rules(workflow, event, step_key)
    if not rules:
        return []

    all_sent = []
    for rule in rules:
        recipients = await resolve_recipients(
            rule.notify, request, get_user_email, get_users_by_role, get_users_by_permission,
        )

        # Determine template
        template_key = event
        kwargs = {}
        if request.token and event in ("submit", "request_corrections"):
            template_key = "token_link" if event == "submit" else "corrections_with_link"
            kwargs["link"] = f"{base_url}/workflow/token/{request.token}"

        subject, body = render_email(template_key, workflow.label, **kwargs)

        for email in recipients:
            await send_mail(email, subject, body, mail_settings)
            all_sent.append(email)

    return all_sent
