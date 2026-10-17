from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BENCHER_", env_nested_delimiter="__", extra="forbid", frozen=True
    )

    @staticmethod
    def home(variable: str, fallback: str) -> Path:
        return Path(os.environ.get(variable) or Path.home() / fallback)

    socket: str | None = Field(default=None, description="Incus or LXD unix socket")
    cache: Path = Field(
        default=home("XDG_CACHE_HOME", ".cache") / "bencher", description="fixture and run cache"
    )

    @classmethod
    def file(cls) -> Path:
        if override := os.environ.get("BENCHER_CONFIG"):
            return Path(override)
        return cls.home("XDG_CONFIG_HOME", ".config") / "bencher" / "config.toml"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls, cls.file()))
