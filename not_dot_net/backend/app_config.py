"""DB-backed config sections with Pydantic schema validation."""

import time

from pydantic import BaseModel
from sqlalchemy import JSON, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base, session_scope


class AppSetting(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "app_setting"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list] = mapped_column(JSON)


_registry: dict[str, "ConfigSection"] = {}

# Sections are read constantly (has_permissions on every permission check, and
# the dashboard badge timer runs that per connected client every 60s) but written
# a handful of times a year, so a short TTL removes nearly every query without
# needing precise invalidation. Single-process only, like the mail outbox.
CACHE_TTL_S = 30.0

# prefix -> (monotonic timestamp, raw JSON). Deliberately NOT the validated model:
# callers mutate what get() hands back (admin_roles assigns into cfg.roles,
# admin_email_templates assigns cfg.layout), so a shared instance would leak
# one admin's unsaved edits to every other reader.
_value_cache: dict[str, tuple[float, dict | list | None]] = {}


def invalidate_config_cache(prefix: str | None = None) -> None:
    """Drop cached values. Call with no argument between tests."""
    if prefix is None:
        _value_cache.clear()
    else:
        _value_cache.pop(prefix, None)


class ConfigSection[T: BaseModel]:
    def __init__(self, prefix: str, schema: type[T], label: str = ""):
        self.prefix = prefix
        self.schema = schema
        self.label = label or prefix.replace("_", " ").title()

    async def get(self) -> T:
        cached = _value_cache.get(self.prefix)
        if cached is not None and (time.monotonic() - cached[0]) < CACHE_TTL_S:
            return self._materialize(cached[1])

        async with session_scope() as session:
            row = await session.get(AppSetting, self.prefix)
            raw = None if row is None else row.value
        _value_cache[self.prefix] = (time.monotonic(), raw)
        return self._materialize(raw)

    def _materialize(self, raw) -> T:
        """Build a fresh model from raw JSON — never share one between callers."""
        return self.schema() if raw is None else self.schema.model_validate(raw)

    async def set(self, value: T) -> None:
        # Invalidate AFTER the write lands, not before: a concurrent get()
        # between an early invalidation and the commit would re-cache the old
        # value and serve it for a whole TTL.
        try:
            await self._write(value.model_dump(mode="json"))
        finally:
            invalidate_config_cache(self.prefix)

    async def _write(self, data: dict) -> None:
        async with session_scope() as session:
            row = await session.get(AppSetting, self.prefix)
            if row:
                row.value = data
                await session.commit()
                return
            session.add(AppSetting(key=self.prefix, value=data))
            try:
                await session.commit()
            except IntegrityError:
                # A concurrent writer inserted this prefix first (one-time race
                # on a brand-new section). Fall back to updating their row.
                await session.rollback()
                row = await session.get(AppSetting, self.prefix)
                if row is not None:
                    row.value = data
                    await session.commit()

    async def reset(self) -> None:
        try:
            async with session_scope() as session:
                row = await session.get(AppSetting, self.prefix)
                if row:
                    await session.delete(row)
                    await session.commit()
        finally:
            invalidate_config_cache(self.prefix)


def section[T: BaseModel](prefix: str, schema: type[T], label: str = "") -> ConfigSection[T]:
    s = ConfigSection(prefix, schema, label)
    _registry[prefix] = s
    return s


def get_registry() -> dict[str, ConfigSection]:
    return _registry
