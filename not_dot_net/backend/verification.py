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

        if req.verification_code_hash and req.code_expires_at:
            expires = req.code_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            # Refuse regeneration while the existing code is still within its
            # expiry window — even after attempts are exhausted. Without this,
            # an attacker can loop generate→try×5→generate forever and
            # brute-force the 10⁶-code space.
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


async def is_locked_out(request_id: uuid.UUID) -> bool:
    """True while attempts are exhausted and the code hasn't expired yet.

    Lets the token page tell 'locked out, wait' apart from 'no code yet' —
    generate_verification_code refuses regeneration in both cases.
    """
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None or not req.verification_code_hash or not req.code_expires_at:
            return False
        if req.code_attempts < MAX_ATTEMPTS:
            return False
        expires = req.code_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < expires


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
        # Row lock: without it N parallel attempts all read code_attempts
        # before any increments, and every one of them clears the gate below.
        req = await session.get(WorkflowRequest, request_id, with_for_update=True)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        if req.code_attempts >= MAX_ATTEMPTS:
            raise PermissionError(
                "Too many attempts — wait for the current code to expire"
            )

        if req.verification_code_hash is None or req.code_expires_at is None:
            return False

        expires = req.code_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            return False

        req.code_attempts += 1

        if _hash_code(code) == req.verification_code_hash:
            # Consume it. Keeping the hash left the code usable for its whole
            # validity window, so anyone who saw it once — shared mailbox,
            # forwarded mail, a glance at the screen — could re-open the form.
            # The caller renders the form straight away and never re-verifies.
            req.verification_code_hash = None
            req.code_expires_at = None
            req.code_attempts = 0
            await session.commit()
            return True

        await session.commit()
        return False
