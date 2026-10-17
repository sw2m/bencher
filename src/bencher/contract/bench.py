from __future__ import annotations

import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Any

from deepmerge import Merger
from pydantic import ByteSize, Field, model_validator

from bencher.contract import Model, Name
from bencher.contract.override import Override

replace = Merger([(dict, ["merge"])], ["override"], ["override"])


class Pin(Model):
    ref: str = Field(min_length=1)


class Fixture(Model):
    repo: str | None = None
    path: str | None = None
    ref: str | None = None
    runner: str = Field(min_length=1)

    @model_validator(mode="after")
    def source(self) -> Fixture:
        if (self.repo is None) == (self.path is None):
            raise ValueError("fixture needs exactly one of repo or path")
        if self.repo is not None and not self.ref:
            raise ValueError("fixture.repo needs a ref")
        return self


class Limits(Model):
    cpu: int = Field(ge=2)  # one CPU is fenced for telemetry
    memory: ByteSize = Field(default=ByteSize(2 * 1024**3))
    provision: timedelta = timedelta(minutes=30)
    run: timedelta = timedelta(hours=1)


class Telemetry(Model):
    interval: timedelta = timedelta(seconds=1)


class Bench(Model):
    bencher: Pin
    fixture: Fixture
    limits: Limits
    telemetry: Telemetry = Telemetry()
    params: dict[str, Any] = {}
    pins: dict[str, str] = {}
    artifacts: dict[Name, str] = {}

    @classmethod
    def load(cls, root: Path, overrides: list[Override]) -> Bench:
        with (root / "bench.toml").open("rb") as file:
            data = tomllib.load(file)
        for override in overrides:
            replace.merge(data, override.tree)
        return cls.model_validate(data)
