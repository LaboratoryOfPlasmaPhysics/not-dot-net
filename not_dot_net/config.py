from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    YamlConfigSettingsSource,
    PydanticBaseSettingsSource,
)


class LDAPSettings(BaseModel):
    url: str = "ldap://localhost"
    base_dn: str = "dc=example,dc=com"
    port: int = 389


class AuthSettings(BaseModel):
    ldap: LDAPSettings = LDAPSettings()


class UsersSettings(BaseModel):
    auth: AuthSettings = AuthSettings()


class BackendSettings(BaseModel):
    users: UsersSettings = UsersSettings()
    database_url: str = "sqlite+aiosqlite:///./test.db"


class Settings(BaseSettings):
    app_name: str = "LPP Intranet"
    admin_email: str = ""
    jwt_secret: str
    storage_secret: str
    backend: BackendSettings = BackendSettings()

    model_config = SettingsConfigDict(yaml_file="config.yaml")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
        )


_settings: Settings | None = None


def init_settings(config_file: str | None = None) -> Settings:
    global _settings
    _settings = Settings(_yaml_file=config_file)
    return _settings


def get_settings() -> Settings:
    if _settings is None:
        raise RuntimeError("Settings not initialized — call init_settings() first")
    return _settings
