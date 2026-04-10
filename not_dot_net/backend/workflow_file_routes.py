"""File download endpoint + request access control helpers."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.users import current_active_user
from not_dot_net.backend.workflow_engine import can_user_act
from not_dot_net.backend.workflow_models import WorkflowFile, WorkflowRequest
from not_dot_net.backend.workflow_service import workflows_config

router = APIRouter(prefix="/workflow", tags=["workflow"])


async def can_view_request(user: User, req: WorkflowRequest) -> bool:
    """Check if user is allowed to view this request."""
    if str(user.id) == str(req.created_by):
        return True
    if await has_permissions(user, "view_audit_log"):
        return True
    cfg = await workflows_config.get()
    wf = cfg.workflows.get(req.type)
    if wf and can_user_act(user, req, wf):
        from not_dot_net.backend.workflow_engine import get_current_step_config
        step = get_current_step_config(req, wf)
        if step and step.assignee_permission:
            return await has_permissions(user, step.assignee_permission)
        return True
    return False


@router.get("/file/{file_id}")
async def download_file(
    file_id: uuid.UUID,
    user: User = Depends(current_active_user),
):
    async with session_scope() as session:
        wf_file = await session.get(WorkflowFile, file_id)
        if wf_file is None:
            raise HTTPException(status_code=404, detail="File not found")
        req = await session.get(WorkflowRequest, wf_file.request_id)
        if req is None:
            raise HTTPException(status_code=404, detail="Request not found")

    if not await can_view_request(user, req):
        raise HTTPException(status_code=403, detail="Access denied")

    path = Path(wf_file.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(path, filename=wf_file.filename)
