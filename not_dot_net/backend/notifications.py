"""Event-driven notification engine for workflow transitions."""

from not_dot_net.config import WorkflowConfig, NotificationRuleConfig, step_display


def _display_from_email(email: str) -> str:
    return (email or "").split("@")[0]


def _resolve_step_label(workflow: WorkflowConfig, step_key: str) -> str:
    """Friendly label for a step key (explicit label or prettified key),
    falling back to the raw key when the step is unknown."""
    step = next((s for s in workflow.steps if s.key == step_key), None)
    return step_display(step) if step else step_key


def _is_target_person(request, email: str) -> bool:
    return bool(request.target_email) and email.lower() == request.target_email.lower()


def _template_for(request, event: str, email: str) -> str:
    """The target person gets their token link whenever one is live — the token
    exists only while their step is current, whatever event moved it there.
    Nobody else ever gets the token."""
    if not (request.token and _is_target_person(request, email)):
        return event
    return "corrections_with_link" if event == "request_corrections" else "token_link"


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
    get_user_email,
    get_users_by_role,
    get_users_by_permission=None,
    get_user_name=None,
) -> list[str]:
    """Fire notifications for a workflow event. Returns list of emails sent to."""
    from not_dot_net.backend.mail import send_mail
    from not_dot_net.backend.email_templates import render_email
    from not_dot_net.config import org_config

    org_cfg = await org_config.get()
    base_url = org_cfg.base_url.rstrip("/")
    app_name = (org_cfg.app_name or "not-dot-net").strip() or "not-dot-net"

    rules = _matching_rules(workflow, event, step_key)
    if not rules:
        return []

    requester_name = ""
    if get_user_name and getattr(request, "created_by", None):
        requester_name = (await get_user_name(request.created_by)) or ""
    step_label = _resolve_step_label(workflow, step_key)

    all_sent = []
    for rule in rules:
        recipients = await resolve_recipients(
            rule.notify, request, get_user_email, get_users_by_role, get_users_by_permission,
        )
        base_ctx = {
            "app_name": app_name,
            "app_url": f"{base_url}/",
            "workflow_label": workflow.label,
            "request_url": f"{base_url}/workflow/request/{request.id}",
            "step_label": step_label,
            "requester_name": requester_name,
        }
        for email in recipients:
            template_key = _template_for(request, event, email)
            ctx = {**base_ctx, "recipient_name": _display_from_email(email)}
            if template_key != event:
                ctx["token_url"] = f"{base_url}/workflow/token/{request.token}"
            subject, body = await render_email(template_key, ctx)
            await send_mail(email, subject, body)
            all_sent.append(email)

    return all_sent
