"""Envelope-encrypted file storage — AES-256-GCM with per-file DEKs."""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import ForeignKey, LargeBinary, String, func, select
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base, session_scope
from not_dot_net.backend.permissions import permission

logger = logging.getLogger(__name__)

ACCESS_PERSONAL_DATA = permission(
    "access_personal_data",
    "Access personal data",
    "View and download encrypted personal documents",
)

ENCRYPTED_DIR = Path("data/encrypted")


def _resolve_encrypted_blob_path(storage_path: str) -> Path:
    encrypted_root = ENCRYPTED_DIR.resolve()
    blob_path = Path(storage_path).resolve()
    if not blob_path.is_relative_to(encrypted_root):
        raise ValueError("Encrypted blob path is outside encrypted storage")
    return blob_path


class EncryptedFile(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "encrypted_file"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    storage_path: Mapped[str] = mapped_column(String(1000))
    original_filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None
    )
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)
    retained_until: Mapped[datetime | None] = mapped_column(nullable=True, default=None)


def _get_master_key() -> bytes:
    """Derive a 32-byte master key from the configured file_encryption_key."""
    from not_dot_net.backend.users import _secrets
    if _secrets is None:
        raise RuntimeError("Secrets not initialized")
    raw_key = _secrets.file_encryption_key
    if not raw_key:
        raise RuntimeError("file_encryption_key not configured in secrets")
    # Encode as UTF-8, then pad or truncate to exactly 32 bytes.
    # For production keys generated via secrets.token_urlsafe(32), this is fine.
    key_bytes = raw_key.encode()
    if len(key_bytes) < 32:
        key_bytes = key_bytes.ljust(32, b"\x00")
    return key_bytes[:32]


def _encrypt_file(data: bytes, master_key: bytes) -> tuple[bytes, bytes, bytes]:
    """Encrypt data with a fresh DEK. Returns (encrypted_data, wrapped_dek, nonce)."""
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    encrypted_data = AESGCM(dek).encrypt(nonce, data, None)
    wrap_nonce = os.urandom(12)
    wrapped_dek = AESGCM(master_key).encrypt(wrap_nonce, dek, None)
    combined_wrapped = wrap_nonce + wrapped_dek
    return encrypted_data, combined_wrapped, nonce


def _decrypt_file(encrypted_data: bytes, wrapped_dek: bytes, nonce: bytes, master_key: bytes) -> bytes:
    """Unwrap DEK and decrypt file data."""
    wrap_nonce = wrapped_dek[:12]
    wrapped = wrapped_dek[12:]
    dek = AESGCM(master_key).decrypt(wrap_nonce, wrapped, None)
    return AESGCM(dek).decrypt(nonce, encrypted_data, None)


async def store_encrypted(
    data: bytes,
    filename: str,
    content_type: str,
    uploaded_by: uuid.UUID | None,
) -> EncryptedFile:
    """Encrypt and store a file. Returns the EncryptedFile record."""
    master_key = _get_master_key()
    encrypted_data, wrapped_dek, nonce = _encrypt_file(data, master_key)

    ENCRYPTED_DIR.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4()
    blob_path = ENCRYPTED_DIR / f"{file_id}.enc"
    blob_path.write_bytes(encrypted_data)

    async with session_scope() as session:
        enc_file = EncryptedFile(
            id=file_id,
            wrapped_dek=wrapped_dek,
            nonce=nonce,
            storage_path=str(blob_path),
            original_filename=filename,
            content_type=content_type,
            uploaded_by=uploaded_by,
        )
        session.add(enc_file)
        await session.commit()
        await session.refresh(enc_file)
        return enc_file


async def read_encrypted(
    file_id: uuid.UUID,
    actor_id: uuid.UUID | str | None = None,
    actor_email: str | None = None,
) -> tuple[bytes, str, str]:
    """Decrypt and return file contents. Logs an audit event.

    Returns (data, original_filename, content_type).
    """
    from not_dot_net.backend.audit import log_audit

    async with session_scope() as session:
        enc_file = await session.get(EncryptedFile, file_id)
        if enc_file is None:
            raise ValueError(f"Encrypted file {file_id} not found")

        master_key = _get_master_key()
        blob_path = _resolve_encrypted_blob_path(enc_file.storage_path)
        if not blob_path.exists():
            raise ValueError(f"Encrypted blob not found on disk: {blob_path}")

        encrypted_data = blob_path.read_bytes()
        data = _decrypt_file(encrypted_data, enc_file.wrapped_dek, enc_file.nonce, master_key)

        await log_audit(
            "personal_data", "download",
            actor_id=actor_id,
            actor_email=actor_email,
            target_type="encrypted_file",
            target_id=file_id,
            detail=f"filename={enc_file.original_filename}",
        )

        return data, enc_file.original_filename, enc_file.content_type


async def mark_for_retention(file_id: uuid.UUID, days: int) -> None:
    """Set the retention deadline on an encrypted file."""
    async with session_scope() as session:
        enc_file = await session.get(EncryptedFile, file_id)
        if enc_file is None:
            return
        enc_file.retained_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)
        await session.commit()


async def delete_expired() -> int:
    """Delete encrypted files past their retention date. Returns count deleted."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    deleted = 0
    async with session_scope() as session:
        result = await session.execute(
            select(EncryptedFile).where(
                EncryptedFile.retained_until != None,  # noqa: E711
                EncryptedFile.retained_until < now,
            )
        )
        for enc_file in result.scalars().all():
            blob_path = _resolve_encrypted_blob_path(enc_file.storage_path)
            if blob_path.exists():
                blob_path.unlink()
            await session.delete(enc_file)
            deleted += 1
        await session.commit()
    return deleted
