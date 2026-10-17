from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from bencher.model import Duration, Key, Model, Name


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
    memory: str = Field(default="2GiB", pattern=r"^\d+(KiB|MiB|GiB|TiB|kB|MB|GB|TB)$")
    provision: Duration = "30m"
    run: Duration = "1h"


class Telemetry(Model):
    interval: Duration = "1s"


class Bench(Model):
    bencher: Pin
    fixture: Fixture
    limits: Limits
    telemetry: Telemetry = Telemetry()
    params: dict[str, Any] = {}
    pins: dict[Key, str] = {}
    artifacts: dict[Name, str] = {}

    @classmethod
    def load(cls, root: Path, overrides: dict[str, Any]) -> Bench:
        with (root / "bench.toml").open("rb") as file:
            data = tomllib.load(file)
        for path, value in overrides.items():
            *parents, leaf = path.split(".")
            node = data
            for part in parents:
                child = node.setdefault(part, {})
                if not isinstance(child, dict):
                    raise ValueError(f"--set {path}: {part} is not a table")
                node = child
            node[leaf] = value
        return cls.model_validate(data)


def override(text: str) -> tuple[str, Any]:
    path, sep, raw = text.partition("=")
    if not sep or not re.fullmatch(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*", path):
        raise ValueError(f"--set {text!r}: expected <dotted.key>=<value>")
    try:
        value = tomllib.loads(f"value = {raw}")["value"]
    except tomllib.TOMLDecodeError:
        value = raw
    return path, value
