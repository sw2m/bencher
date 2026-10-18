from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator
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
    l1: str = Field(
        default="https://github.com/sw2m/bencher/releases/download/v{version}/{file}",
        description="where L1 runner images are fetched from; {version} and {file} are filled in",
    )

    @field_validator("l1")
    @classmethod
    def template(cls, value: str) -> str:
        if "{file}" not in value:
            raise ValueError("l1 must be a URL template containing {file}")
        try:
            value.format(version="0", file="manifest.json")
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError(
                f"l1 is not a valid template ({error!r}); it may use only {{version}} and {{file}}"
            ) from None
        return value

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
