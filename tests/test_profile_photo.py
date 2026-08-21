from not_dot_net.backend.permissions import SYSTEM_ACTOR
from io import BytesIO

from PIL import Image

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.profile_photo import (
    PROFILE_PHOTO_MAX_DIMENSION_PX,
    profile_photo_data_uri,
    profile_photo_max_bytes,
    profile_photo_mime,
    remove_profile_photo,
    save_profile_photo,
    validate_profile_photo,
)


def _image_bytes(fmt: str, size: tuple[int, int] = (64, 64), mode: str = "RGB") -> bytes:
    image = Image.new(mode, size, (200, 40, 80) if mode == "RGB" else (200, 40, 80, 128))
    output = BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


JPEG_BYTES = _image_bytes("JPEG")
PNG_BYTES = _image_bytes("PNG")


def test_profile_photo_mime_detects_supported_images():
    assert profile_photo_mime(JPEG_BYTES) == "image/jpeg"
    assert profile_photo_mime(PNG_BYTES) == "image/png"


def test_profile_photo_mime_rejects_unknown_content():
    assert profile_photo_mime(b"not an image") is None


def test_profile_photo_data_uri_uses_detected_mime_type():
    assert profile_photo_data_uri(PNG_BYTES).startswith("data:image/png;base64,")
    assert profile_photo_data_uri(JPEG_BYTES).startswith("data:image/jpeg;base64,")


def test_validate_profile_photo_accepts_jpg_and_png():
    assert validate_profile_photo(JPEG_BYTES, "avatar.jpg") is None
    assert validate_profile_photo(JPEG_BYTES, "avatar.jpeg") is None
    assert validate_profile_photo(PNG_BYTES, "avatar.png") is None


def test_validate_profile_photo_rejects_bad_extension():
    assert validate_profile_photo(PNG_BYTES, "avatar.gif") == "profile_photo_invalid_type"


def test_validate_profile_photo_rejects_content_mismatch():
    assert validate_profile_photo(b"\x89PNG\r\n\x1a\nnot an image", "avatar.png") == "profile_photo_invalid_content"


def test_validate_profile_photo_rejects_decompression_bomb():
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 100
    try:
        content = _image_bytes("PNG", size=(20, 20))
        assert validate_profile_photo(content, "avatar.png") == "profile_photo_invalid_content"
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def test_validate_profile_photo_rejects_large_file():
    content = JPEG_BYTES + b"x" * profile_photo_max_bytes(1)
    assert validate_profile_photo(content, "avatar.jpg", max_size_mb=1) == "profile_photo_too_large"


def test_profile_photo_max_bytes_uses_megabytes():
    assert profile_photo_max_bytes(2) == 2 * 1024 * 1024


async def test_save_and_remove_profile_photo():
    async with session_scope() as session:
        user = User(email="photo@test.dev", hashed_password="x")
        session.add(user)
        await session.commit()
        user_id = user.id

    saved = await save_profile_photo(user_id, PNG_BYTES, actor=SYSTEM_ACTOR)
    assert saved is not None
    assert profile_photo_mime(saved) == "image/jpeg"

    async with session_scope() as session:
        user = await session.get(User, user_id)
        assert user.photo == saved

    assert await remove_profile_photo(user_id, actor=SYSTEM_ACTOR) is True

    async with session_scope() as session:
        user = await session.get(User, user_id)
        assert user.photo is None


async def test_save_profile_photo_stores_thumbnail():
    original = _image_bytes("JPEG", size=(1200, 800))
    async with session_scope() as session:
        user = User(email="thumbnail@test.dev", hashed_password="x")
        session.add(user)
        await session.commit()
        user_id = user.id

    saved = await save_profile_photo(user_id, original, actor=SYSTEM_ACTOR)

    assert saved is not None
    assert len(saved) < len(original)
    with Image.open(BytesIO(saved)) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= PROFILE_PHOTO_MAX_DIMENSION_PX


import uuid

import pytest


async def _make_user(email: str, *, role: str = "", superuser: bool = False) -> User:
    async with session_scope() as session:
        user = User(
            id=uuid.uuid4(), email=email, hashed_password="x",
            role=role, is_superuser=superuser,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_save_profile_photo_rejects_other_users_without_permission():
    """R-10: the service must enforce who may change whose photo — the
    frontend's render-time gating is not enough."""
    target = await _make_user("photo-target@test.com")
    actor = await _make_user("photo-actor@test.com")

    with pytest.raises(PermissionError):
        await save_profile_photo(target.id, JPEG_BYTES, actor=actor)
    with pytest.raises(PermissionError):
        await remove_profile_photo(target.id, actor=actor)


async def test_save_profile_photo_allows_self_and_superuser():
    target = await _make_user("photo-self@test.com")
    admin = await _make_user("photo-admin@test.com", superuser=True)

    assert await save_profile_photo(target.id, JPEG_BYTES, actor=target) is not None
    assert await save_profile_photo(target.id, JPEG_BYTES, actor=admin) is not None
    assert await remove_profile_photo(target.id, actor=admin) is True
