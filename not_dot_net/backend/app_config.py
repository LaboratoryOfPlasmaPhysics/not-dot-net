"""DB-backed config sections with Pydantic schema validation."""

from pydantic import BaseModel
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base, session_scope


class AppSetting(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "app_setting"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list] = mapped_column(JSON)


_registry: dict[str, "ConfigSection"] = {}


class ConfigSection[T: BaseModel]:
    def __init__(self, prefix: str, schema: type[T], label: str = ""):
        self.prefix = prefix
        self.schema = schema
        self.label = label or prefix.replace("_", " ").title()

    async def get(self) -> T:
        async with session_scope() as session:
            row = await session.get(AppSetting, self.prefix)
            if row is None:
                return self.schema()
            return self.schema.model_validate(row.value)

    async def set(self, value: T) -> None:
        data = value.model_dump(mode="json")
        async with session_scope() as session:
            row = await session.get(AppSetting, self.prefix)
            if row:
                row.value = data
            else:
                session.add(AppSetting(key=self.prefix, value=data))
            await session.commit()

    async def reset(self) -> None:
        async with session_scope() as session:
            row = await session.get(AppSetting, self.prefix)
            if row:
                await session.delete(row)
                await session.commit()


def section[T: BaseModel](prefix: str, schema: type[T], label: str = "") -> ConfigSection[T]:
    s = ConfigSection(prefix, schema, label)
    _registry[prefix] = s
    return s


def get_registry() -> dict[str, ConfigSection]:
    return _registry
