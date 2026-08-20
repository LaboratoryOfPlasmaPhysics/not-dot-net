"""Workflow file uploads — validation, on-disk layout and persistence.

`UPLOAD_ROOT` is read through this module (never copied into a caller's
namespace) so that tests patching it here reach every writer.
"""

import logging
import os
import uuid
from pathlib import Path

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import (
    RequestStatus,
    WorkflowFile,
    WorkflowRequest,
)

logger = logging.getLogger(__name__)

UPLOAD_ROOT = Path(os.environ.get("NDN_DATA_DIR", "data")) / "uploads"


def _safe_upload_path(stored_path: str, root: Path | None = None) -> Path:
    """Resolve a WorkflowFile.storage_path and confirm it sits under the
    upload root. Defends against corrupted DB rows pointing outside the
    expected directory.

    Returns the resolved absolute Path. Raises ValueError otherwise.
    """
    base = (root or UPLOAD_ROOT).resolve()
    candidate = Path(stored_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"Refusing to serve file outside upload root: {stored_path!r}"
        ) from exc
    return candidate




ALLOWED_EXTENSIONS: set[str] = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"}

# Magic bytes → expected extensions
_MAGIC_SIGNATURES: list[tuple[bytes, set[str]]] = [
    (b"%PDF", {".pdf"}),
    (b"\xff\xd8\xff", {".jpg", ".jpeg"}),
    (b"\x89PNG\r\n\x1a\n", {".png"}),
    (b"PK\x03\x04", {".docx"}),  # ZIP-based (OOXML)
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", {".doc"}),  # OLE2 (legacy Word)
]


def _check_magic(content: bytes, ext: str) -> bool:
    """Check if file content matches its extension via magic bytes."""
    for signature, valid_exts in _MAGIC_SIGNATURES:
        if content[:len(signature)] == signature:
            return ext in valid_exts
    return True  # no signature match → skip check (don't block unknown formats)


def validate_upload(content: bytes, filename: str, content_type: str, max_size_mb: int) -> str | None:
    """Validate file upload. Returns error message or None if valid."""
    max_bytes = max_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        return f"File too large (max {max_size_mb} MB)"
    from pathlib import PurePosixPath
    ext = PurePosixPath(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return f"File type not allowed: {ext}"
    if not _check_magic(content, ext):
        return "File content does not match its extension"
    return None


async def persist_workflow_upload(
    *,
    request_id: uuid.UUID,
    step_key: str,
    field_name: str,
    content: bytes,
    filename: str,
    content_type: str,
    encrypted: bool,
    uploaded_by: uuid.UUID | None,
    expected_step_key: str | None = None,
) -> WorkflowFile:
    """Persist one uploaded file as a new WorkflowFile (a new current version).

    Plain files go to UPLOAD_ROOT/<request_id>/<file_id>/<filename> — a unique
    folder per upload — so a re-upload, or a same-named file in another field,
    never overwrites an earlier version's blob on disk. Encrypted files already
    get a unique blob per call via store_encrypted.

    `expected_step_key` closes the gap between a caller's own staleness check
    and this write: the request is locked and re-checked inside the same
    transaction, so a submit_step landing in between cannot leave the file
    attached to a step that has already been reviewed.
    """
    # Reduce to a basename at the sink so a '..'-laden name can never escape the
    # per-upload directory, regardless of what a caller passes.
    filename = Path(filename).name
    file_id = uuid.uuid4()
    written_paths: list[Path] = []
    async with session_scope() as session:
        if expected_step_key is not None:
            req = await session.get(WorkflowRequest, request_id, with_for_update=True)
            if req is None:
                raise PermissionError("Request no longer exists")
            if req.status != RequestStatus.IN_PROGRESS:
                raise PermissionError("Request is no longer open for uploads")
            if req.current_step != expected_step_key:
                raise PermissionError(
                    f"Request moved to step '{req.current_step}' — "
                    f"upload for '{expected_step_key}' rejected"
                )
        try:
            if encrypted:
                from not_dot_net.backend.encrypted_storage import prepare_encrypted_file_record
                enc_file, blob_path = prepare_encrypted_file_record(
                    content, filename, content_type, uploaded_by,
                )
                written_paths.append(blob_path)
                session.add(enc_file)
                wf_file = WorkflowFile(
                    id=file_id, request_id=request_id, step_key=step_key,
                    field_name=field_name, filename=filename, storage_path="encrypted",
                    uploaded_by=uploaded_by, encrypted_file_id=enc_file.id,
                )
            else:
                dest_dir = UPLOAD_ROOT / str(request_id) / str(file_id)
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / filename
                dest.write_bytes(content)
                written_paths.append(dest)
                wf_file = WorkflowFile(
                    id=file_id, request_id=request_id, step_key=step_key,
                    field_name=field_name, filename=filename, storage_path=str(dest),
                    uploaded_by=uploaded_by,
            )
            session.add(wf_file)
            await session.commit()
            return wf_file
        except Exception:
            await session.rollback()
            for path in written_paths:
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    logger.exception("Failed to clean up workflow upload path %s", path)
            raise

