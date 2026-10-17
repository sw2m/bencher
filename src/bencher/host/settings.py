from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


def home(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable) or Path.home() / fallback)


def config() -> Path:
    if override := os.environ.get("BENCHER_CONFIG"):
        return Path(override)
    return home("XDG_CONFIG_HOME", ".config") / "bencher" / "config.toml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BENCHER_", extra="forbid", frozen=True)

    socket: str | None = Field(
        default=None, validation_alias=AliasChoices("socket", "BENCHER_SOCKET", "INCUS_SOCKET")
    )
    cache: Path = Field(default_factory=lambda: home("XDG_CACHE_HOME", ".cache") / "bencher")
    keep: bool = False
    out: Path | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls, config()))

    @classmethod
    def layered(cls, flags: dict[str, Any]) -> Settings:
        return cls(**{k: v for k, v in flags.items() if v is not None})
