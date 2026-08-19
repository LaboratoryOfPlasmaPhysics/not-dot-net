"""P10/P11 — Pillow decode/resize/encode and disk reads ran on the event loop.

Every profile-photo and floor-plan upload spent tens to hundreds of ms decoding,
LANCZOS-resizing and re-encoding inside the single loop that also serves every
connected client's websocket.
"""
import asyncio
import time
from io import BytesIO

import pytest
from PIL import Image

TICK = 0.01


async def _ticks_during(coro) -> tuple[int, object]:
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(TICK)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        result = await coro
    finally:
        task.cancel()
    return ticks, result


def _big_image(px: int = 2600) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (px, px), "white").save(buf, format="PNG")
    return buf.getvalue()


async def test_profile_photo_processing_does_not_block_the_loop():
    from not_dot_net.backend.profile_photo import process_profile_photo_async

    ticks, processed = await _ticks_during(process_profile_photo_async(_big_image()))
    assert processed is not None
    assert ticks > 2, f"event loop stalled during photo processing ({ticks} ticks)"


async def test_floorplan_processing_does_not_block_the_loop():
    from not_dot_net.backend.floorplan_service import process_floorplan_image_async

    ticks, processed = await _ticks_during(process_floorplan_image_async(_big_image()))
    assert processed is not None
    assert ticks > 2, f"event loop stalled during floor-plan processing ({ticks} ticks)"


async def test_async_wrappers_agree_with_the_sync_versions():
    from not_dot_net.backend.floorplan_service import (
        process_floorplan_image, process_floorplan_image_async,
    )
    from not_dot_net.backend.profile_photo import (
        process_profile_photo, process_profile_photo_async,
    )

    data = _big_image(600)
    assert await process_profile_photo_async(data) == process_profile_photo(data)
    assert await process_floorplan_image_async(data) == process_floorplan_image(data)


async def test_rejects_garbage_the_same_way():
    from not_dot_net.backend.floorplan_service import process_floorplan_image_async
    from not_dot_net.backend.profile_photo import process_profile_photo_async

    assert await process_profile_photo_async(b"not an image") is None
    assert await process_floorplan_image_async(b"not an image") is None
