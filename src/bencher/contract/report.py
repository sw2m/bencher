from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from bencher.contract import Model
from bencher.contract.result import Row
from bencher.measure.stats import Stats
from bencher.measure.telemetry import Series

Stage = Literal["load", "stage", "launch", "provision", "execute", "harvest", "report"]


class Line(Row):
    stats: dict[str, Stats] | None = None
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
    overrides: dict[str, Any] = {}
    rows: list[Line] = []
    system: dict[str, Series] = {}
    expectations: list[Outcome] = []
    errors: list[Error] = []

    def dump(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2) + "\n"
