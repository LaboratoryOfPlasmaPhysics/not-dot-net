"""Runtime-editable app settings stored in DB, with config file defaults."""

from not_dot_net.backend.app_config import AppSetting  # noqa: F401 — re-export
from not_dot_net.backend.db import session_scope
from not_dot_net.config import get_settings


async def _get(key: str):
    async with session_scope() as session:
        row = await session.get(AppSetting, key)
        return row.value if row else None


async def _set(key: str, value):
    async with session_scope() as session:
        row = await session.get(AppSetting, key)
        if row:
            row.value = value
        else:
            session.add(AppSetting(key=key, value=value))
        await session.commit()


async def get_os_choices() -> list[str]:
    val = await _get("os_choices")
    return val if val is not None else get_settings().os_choices


async def set_os_choices(choices: list[str]) -> None:
    await _set("os_choices", choices)


async def get_software_tags() -> dict[str, list[str]]:
    val = await _get("software_tags")
    return val if val is not None else get_settings().software_tags


async def set_software_tags(tags: dict[str, list[str]]) -> None:
    await _set("software_tags", tags)
