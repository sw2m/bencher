from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, field_validator, model_validator

from bencher.model import Key, Model
from bencher.stats import Stats
from bencher.telemetry import Series

Verdict = Literal["ok", "err"]
Stage = Literal["load", "payload", "stage", "launch", "provision", "execute", "harvest", "report"]


class Row(Model):
    name: Key
    tags: dict[Key, Key] = {}
    reporter: str | None = None
    samples: dict[Key, list[float]] | None = None
    verdict: Verdict | None = None

    @model_validator(mode="after")
    def kind(self) -> Row:
        if (self.samples is None) == (self.verdict is None):
            raise ValueError("a row carries exactly one of samples or verdict")
        for unit, values in (self.samples or {}).items():
            if not values:
                raise ValueError(f"samples[{unit!r}] is empty")
            if not all(math.isfinite(v) for v in values):
                raise ValueError(f"samples[{unit!r}] has a non-finite value")
        return self

    def key(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        return (self.name, tuple(sorted(self.tags.items())))


class Result(Model):
    rows: list[Row]

    @field_validator("rows")
    @classmethod
    def unique(cls, rows: list[Row]) -> list[Row]:
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        for row in rows:
            if row.key() in seen:
                raise ValueError(f"duplicate row key: {row.name} {dict(row.tags)}")
            seen.add(row.key())
        return rows

    @classmethod
    def parse(cls, text: str) -> Result:
        return cls.model_validate_json(text)


class Line(Row):
    stats: dict[Key, Stats] | None = None
    telemetry: dict[str, Series] | None = None


class Check(Model):
    tags: dict[str, str]
    actual: float | str | None
    bound: float | str | None
    passed: bool = Field(alias="pass")


class Outcome(Model):
    line: int
    rule: str
    checks: list[Check]
    passed: bool = Field(alias="pass")
    error: str | None


class Error(Model):
    stage: Stage
    message: str


class Report(Model):
    passed: bool = Field(alias="pass")
    rows: list[Line] = []
    system: dict[str, Series] = {}
    expectations: list[Outcome] = []
    errors: list[Error] = []

    def dump(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2) + "\n"
