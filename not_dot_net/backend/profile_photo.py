import base64
from io import BytesIO
import uuid
from pathlib import PurePosixPath

from PIL import Image, ImageOps, UnidentifiedImageError

from not_dot_net.backend.db import User, session_scope
from not_dot_net.config import DEFAULT_PROFILE_PHOTO_MAX_SIZE_MB, files_config


ALLOWED_PROFILE_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png"}
PROFILE_PHOTO_MAX_DIMENSION_PX = 256
PROFILE_PHOTO_JPEG_QUALITY = 85


def profile_photo_max_bytes(max_size_mb: int) -> int:
    return max_size_mb * 1024 * 1024


def profile_photo_mime(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def profile_photo_data_uri(content: bytes | None) -> str | None:
    if not content:
        return None
    mime = profile_photo_mime(content)
    if mime is None:
        return None
    b64 = base64.b64encode(content).decode()
    return f"data:{mime};base64,{b64}"


def _rgb_profile_photo(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        background = Image.new("RGB", image.size, "white")
        alpha = image.convert("RGBA").getchannel("A")
        background.paste(image.convert("RGBA"), mask=alpha)
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def process_profile_photo(content: bytes) -> bytes | None:
    try:
        with Image.open(BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(
                (PROFILE_PHOTO_MAX_DIMENSION_PX, PROFILE_PHOTO_MAX_DIMENSION_PX),
                Image.Resampling.LANCZOS,
            )
            image = _rgb_profile_photo(image)

            output = BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=PROFILE_PHOTO_JPEG_QUALITY,
                optimize=True,
            )
            return output.getvalue()
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
        return None


def validate_profile_photo(
    content: bytes,
    filename: str,
    max_size_mb: int = DEFAULT_PROFILE_PHOTO_MAX_SIZE_MB,
) -> str | None:
    if len(content) > profile_photo_max_bytes(max_size_mb):
        return "profile_photo_too_large"

    ext = PurePosixPath(filename).suffix.lower()
    if ext not in ALLOWED_PROFILE_PHOTO_EXTENSIONS:
        return "profile_photo_invalid_type"

    if profile_photo_mime(content) is None or process_profile_photo(content) is None:
        return "profile_photo_invalid_content"

    return None


async def validate_profile_photo_upload(content: bytes, filename: str) -> str | None:
    cfg = await files_config.get()
    return validate_profile_photo(content, filename, cfg.profile_photo_max_size_mb)


async def save_profile_photo(user_id: uuid.UUID, content: bytes) -> bytes | None:
    processed = process_profile_photo(content)
    if processed is None:
        return None

    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            return None
        user.photo = processed
        await session.commit()
    return processed


async def remove_profile_photo(user_id: uuid.UUID) -> bool:
    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            return False
        user.photo = None
        await session.commit()
    return True
