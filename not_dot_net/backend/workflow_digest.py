"""Remind each approver, on set weekdays, of the requests waiting on them.

The dashboard turns a card red, but only for someone who opens the app.
"""

import logging
from datetime import date

from sqlalchemy import select

from not_dot_net.backend import mail
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.email_templates import render_email
from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.backend.workflow_engine import get_current_step_config, request_display_name
from not_dot_net.backend.workflow_service import compute_step_age_days, list_actionable, list_events_batch
from not_dot_net.config import dashboard_config, org_config, step_display

logger = logging.getLogger("not_dot_net.workflow_digest")


async def send_pending_digests(today: date | None = None) -> int:
    """Queue one email per user with pending requests. Returns how many were queued."""
    today = today or date.today()
    if today.weekday() not in (await dashboard_config.get()).digest_weekdays:
        return 0
    queued = 0
    for user in await _active_users():
        items = await _pending_items(user)
        if items:
            await _send(user, items)
            queued += 1
    return queued


async def run_pending_digest_job() -> None:
    """APScheduler entrypoint."""
    try:
        queued = await send_pending_digests()
        if queued:
            logger.info("Queued %d pending-request digest(s)", queued)
    except Exception:
        logger.exception("Pending-request digest job failed")


async def _active_users() -> list[User]:
    async with session_scope() as session:
        rows = await session.scalars(select(User).where(User.is_active.is_(True), User.email != ""))
        return list(rows)


async def _pending_items(user) -> list[dict]:
    requests = await list_actionable(user)
    if not requests:
        return []
    workflows = (await workflows_config.get()).workflows
    events = await list_events_batch([r.id for r in requests])
    base_url = (await org_config.get()).base_url.rstrip("/")
    items = []
    for req in requests:
        wf = workflows.get(req.type)
        step = get_current_step_config(req, wf) if wf else None
        items.append({
            "label": f"{wf.label if wf else req.type} — {request_display_name(req)}",
            "step": step_display(step) if step else req.current_step,
            "age_days": compute_step_age_days(events.get(req.id, []), req.current_step),
            "url": f"{base_url}/workflow/request/{req.id}",
        })
    return sorted(items, key=lambda i: i["age_days"], reverse=True)


async def _send(user, items: list[dict]) -> None:
    org = await org_config.get()
    subject, body = await render_email("pending_digest", {
        "app_name": org.app_name,
        "app_url": f"{org.base_url.rstrip('/')}/",
        "recipient_name": user.full_name or user.email,
        "count": len(items),
        "oldest_days": items[0]["age_days"],
        "items": items,
    })
    await mail.send_mail(user.email, subject, body)
