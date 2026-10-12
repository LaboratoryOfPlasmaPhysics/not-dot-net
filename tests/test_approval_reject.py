"""Rejecting a request needs a reason and a confirmation.

Review 2026-10-12 U4: Reject fired on one click with no confirmation and no
comment, so the requester was told "rejected" with no explanation.
"""

import asyncio
from types import SimpleNamespace

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.config import WorkflowStepConfig
from not_dot_net.frontend.workflow_step import render_approval

STEP = WorkflowStepConfig(key="review", type="approval", actions=["approve", "reject"])
WORKFLOW = SimpleNamespace(label="VPN Access Request")


async def _open_approval(user: UiUser, path: str, rejected: list):
    async def on_reject(comment):
        rejected.append(comment)

    async def on_approve(comment):
        pass

    @ui.page(path)
    def page():
        render_approval({}, WORKFLOW, STEP, on_approve, on_reject)

    await user.open(path)


async def test_reject_without_comment_is_refused(user: UiUser):
    rejected = []
    await _open_approval(user, "/reject-needs-comment", rejected)
    user.find(marker="reject").click()
    await user.should_see("A reason is required to reject: please fill in the comment.")
    assert rejected == []


async def test_reject_with_comment_asks_for_confirmation(user: UiUser):
    rejected = []
    await _open_approval(user, "/reject-confirms", rejected)
    user.find(marker="comment").type("Missing signature")
    user.find(marker="reject").click()
    await user.should_see("Reject this request? The requester will be notified with your comment.")
    assert rejected == []

    user.find("Confirm").click()
    await asyncio.sleep(0.05)
    assert rejected == ["Missing signature"]
