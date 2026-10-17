from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from bencher.contract import Model, Name


class Runner(Model):
    image: str = Field(min_length=1)
    type: Literal["container", "vm"] = "container"
    reporters: list[Name] = []

    @field_validator("reporters")
    @classmethod
    def unique(cls, reporters: list[str]) -> list[str]:
        if "system" in reporters:
            raise ValueError("reporter 'system' is reserved for system-wide telemetry")
        if len(set(reporters)) != len(reporters):
            raise ValueError("reporters must be unique")
        return reporters

    @classmethod
    def load(cls, root: Path) -> Runner:
        with (root / "runner.toml").open("rb") as file:
            return cls.model_validate(tomllib.load(file))
