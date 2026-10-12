"""File uploads for a workflow form step, shared by the token page and the
logged-in detail page. Each caller supplies its own authorization re-check."""

import uuid
from pathlib import Path
from typing import Awaitable, Callable

from nicegui import ui

from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.backend.workflow_files import current_files_by_name, load_files
from not_dot_net.backend.workflow_uploads import persist_workflow_upload, validate_upload
from not_dot_net.frontend.i18n import t


async def make_upload_handler(
    request_id: uuid.UUID,
    step,
    *,
    encrypted_fields: set[str],
    uploaded_by: uuid.UUID | None,
    authorize: Callable[[], Awaitable[bool]],
    denied_message: str,
):
    """Return ({field: current filename}, async handler(field_name, event)).

    `authorize` runs before every upload: a stale tab must not upload into a
    request that advanced or completed since the page was opened (the new file
    would displace the reviewed "current" version). `persist_workflow_upload`
    re-checks the step under a lock at the write itself.
    """
    existing = await load_files(request_id, step.key)
    uploaded_files = {name: f.filename for name, f in current_files_by_name(existing).items()}
    max_upload_size_mb = (await workflows_config.get()).max_upload_size_mb

    async def handle_file_upload(field_name, event):
        if not await authorize():
            ui.notify(t(denied_message), color="negative")
            return
        upload = event.file
        content = await upload.read()
        # Basename only — never trust the client to provide a path.
        filename = Path(upload.name).name
        content_type = upload.content_type or "application/octet-stream"

        error = validate_upload(content, filename, content_type, max_upload_size_mb)
        if error:
            ui.notify(error, color="negative")
            return

        try:
            await persist_workflow_upload(
                request_id=request_id,
                step_key=step.key,
                field_name=field_name,
                content=content,
                filename=filename,
                content_type=content_type,
                encrypted=field_name in encrypted_fields,
                uploaded_by=uploaded_by,
                expected_step_key=step.key,
            )
        except PermissionError:
            ui.notify(t(denied_message), color="negative")
            return

        uploaded_files[field_name] = filename
        ui.notify(t("uploaded").format(filename=filename), color="positive")

    return uploaded_files, handle_file_upload
