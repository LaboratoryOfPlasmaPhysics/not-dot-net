"""Floor plan service — plan image CRUD and map point placement."""

import os
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError, ImageOps
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.floorplan_models import FloorPlan
from not_dot_net.backend.permissions import check_permission, permission

MANAGE_FLOORPLANS = permission(
    "manage_floorplans", "Manage floor plans",
    "Upload floor plans and place map points",
)

FLOORPLAN_ROOT = Path(os.environ.get("NDN_DATA_DIR", "data")) / "floorplans"
FLOORPLAN_MAX_DIMENSION_PX = 2400
FLOORPLAN_JPEG_QUALITY = 90


def _rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        background = Image.new("RGB", image.size, "white")
        alpha = image.convert("RGBA").getchannel("A")
        background.paste(image.convert("RGBA"), mask=alpha)
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def process_floorplan_image(content: bytes) -> tuple[bytes, int, int] | None:
    """Validate + normalize an uploaded floor plan image.

    Re-encodes as JPEG, capped to FLOORPLAN_MAX_DIMENSION_PX on the long
    side, so map-point pixel coordinates and the base64 payload sent to the
    browser stay bounded regardless of what an admin uploads.
    """
    try:
        with Image.open(BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(
                (FLOORPLAN_MAX_DIMENSION_PX, FLOORPLAN_MAX_DIMENSION_PX),
                Image.Resampling.LANCZOS,
            )
            image = _rgb(image)
            output = BytesIO()
            image.save(output, format="JPEG", quality=FLOORPLAN_JPEG_QUALITY, optimize=True)
            return output.getvalue(), image.width, image.height
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
        return None


async def create_floor_plan(name: str, content: bytes, actor=None) -> FloorPlan:
    if actor is not None:
        await check_permission(actor, MANAGE_FLOORPLANS)
    processed = process_floorplan_image(content)
    if processed is None:
        raise ValueError("Invalid image")
    jpeg_bytes, width, height = processed

    floor_plan_id = uuid.uuid4()
    FLOORPLAN_ROOT.mkdir(parents=True, exist_ok=True)
    image_path = FLOORPLAN_ROOT / f"{floor_plan_id}.jpg"
    image_path.write_bytes(jpeg_bytes)

    async with session_scope() as session:
        fp = FloorPlan(
            id=floor_plan_id, name=name, image_path=str(image_path),
            width_px=width, height_px=height,
        )
        session.add(fp)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            image_path.unlink(missing_ok=True)
            raise ValueError(f"Floor plan name '{name}' already exists") from exc
        await session.refresh(fp)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "floorplan", "create",
        actor_id=(actor.id if actor else None),
        target_type="floor_plan", target_id=fp.id,
        detail=f"name={name}",
    )
    return fp


async def list_floor_plans(active_only: bool = True) -> list[FloorPlan]:
    async with session_scope() as session:
        query = select(FloorPlan).order_by(FloorPlan.name)
        if active_only:
            query = query.where(FloorPlan.active == True)  # noqa: E712
        return list((await session.execute(query)).scalars().all())


async def get_floor_plan_image(floor_plan_id: uuid.UUID) -> bytes | None:
    async with session_scope() as session:
        fp = await session.get(FloorPlan, floor_plan_id)
        if fp is None:
            return None
    path = Path(fp.image_path)
    if not path.is_file():
        return None
    return path.read_bytes()


async def delete_floor_plan(floor_plan_id: uuid.UUID, actor=None) -> None:
    if actor is not None:
        await check_permission(actor, MANAGE_FLOORPLANS)
    async with session_scope() as session:
        fp = await session.get(FloorPlan, floor_plan_id)
        if fp is None:
            raise ValueError(f"Floor plan {floor_plan_id} not found")
        deleted_name = fp.name
        image_path = Path(fp.image_path)
        await session.delete(fp)
        await session.commit()
    image_path.unlink(missing_ok=True)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "floorplan", "delete",
        actor_id=(actor.id if actor else None),
        target_type="floor_plan", target_id=floor_plan_id,
        detail=f"name={deleted_name}",
    )
