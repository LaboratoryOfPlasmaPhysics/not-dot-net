"""Verification code service — OTP-style email verification for token pages."""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowRequest

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def generate_verification_code(request_id: uuid.UUID) -> str | None:
    """Generate a 6-digit code, store its hash, return the plaintext.

    Returns None if a valid unexpired code already exists (caller should
    tell the user to check their email).
    """
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        if req.verification_code_hash and req.code_expires_at and req.code_attempts < MAX_ATTEMPTS:
            expires = req.code_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < expires:
                return None

        from not_dot_net.backend.workflow_service import workflows_config
        wf_cfg = await workflows_config.get()

        code = f"{secrets.randbelow(1_000_000):06d}"
        req.verification_code_hash = _hash_code(code)
        req.code_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=wf_cfg.verification_code_expiry_minutes)
        req.code_attempts = 0
        await session.commit()

    return code


async def has_valid_code(request_id: uuid.UUID) -> bool:
    """Check if a valid unexpired code exists for this request."""
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None or not req.verification_code_hash or not req.code_expires_at:
            return False
        if req.code_attempts >= MAX_ATTEMPTS:
            return False
        expires = req.code_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < expires


async def verify_code(request_id: uuid.UUID, code: str) -> bool:
    """Verify a code. Returns True on match. Raises PermissionError if rate-limited."""
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        if req.code_attempts >= MAX_ATTEMPTS:
            raise PermissionError("Too many attempts — request a new code")

        if req.verification_code_hash is None or req.code_expires_at is None:
            return False

        expires = req.code_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            return False

        req.code_attempts += 1

        if _hash_code(code) == req.verification_code_hash:
            req.code_attempts = 0
            await session.commit()
            return True

        await session.commit()
        return False
