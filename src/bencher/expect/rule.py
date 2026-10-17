from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Any, Literal

from pydantic import Json, model_validator

from bencher.contract import Model
from bencher.contract.report import Check, Line, Outcome
from bencher.contract.result import Verdict
from bencher.measure.stats import Stats
from bencher.measure.telemetry import Summary

ops: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    ">": operator.gt,
}
fields = {"samples": Stats.scalars(), "telemetry": Summary.scalars()}


class Quantity(Model):
    kind: Literal["verdict", "samples", "telemetry"]
    key: str = ""
    stat: str = ""

    @model_validator(mode="before")
    @classmethod
    def parse(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if value == "verdict":
            return {"kind": "verdict"}
        kind, *middle, stat = value.split(".")
        key = ".".join(middle)
        if kind not in fields or not key or stat not in fields[kind]:
            raise ValueError(f"bad quantity {value!r}")
        return {"kind": kind, "key": key, "stat": stat}

    def read(self, row: Line) -> float | str | None:
        if self.kind == "verdict":
            return row.verdict
        if self.kind == "samples":
            stats = (row.stats or {}).get(self.key)
            value = getattr(stats, self.stat) if stats else None
        else:
            series = (row.telemetry or {}).get(self.key)
            summary = series.summary if series else None
            value = getattr(summary, self.stat) if summary else None
        return None if value is None else float(value)


class Relative(Model):
    times: float
    of: dict[str, str]


class Rule(Model):
    line: int
    raw: str
    name: str
    tags: Json[dict[str, str]]
    quantity: Quantity
    op: Literal["<", "<=", "==", "!=", ">=", ">"]
    bound: Json[float | Verdict | Relative]

    @model_validator(mode="after")
    def coherent(self) -> Rule:
        verdict = self.quantity.kind == "verdict"
        if verdict != isinstance(self.bound, str):
            raise ValueError('verdict rules take an "ok"/"err" bound, and only they do')
        if verdict and self.op not in ("==", "!="):
            raise ValueError("verdict rules compare with == or !=")
        return self

    def matches(self, row: Line) -> bool:
        return row.name == self.name and all(row.tags.get(k) == v for k, v in self.tags.items())

    def owned(self) -> bool:
        return self.quantity.kind == "verdict" and self.op == "=="

    def check(self, rows: list[Line]) -> Outcome:
        matched = [row for row in rows if self.matches(row)]
        if not matched:
            return Outcome(
                line=self.line, rule=self.raw, checks=[], passed=False, error="matches no row"
            )
        index = {row.key(): row for row in rows}
        checks = []
        for row in matched:
            actual = self.quantity.read(row)
            bound: float | str | None
            if isinstance(self.bound, Relative):
                tags = {**row.tags, **self.bound.of}
                ref = index.get((row.name, tuple(sorted(tags.items()))))
                base = self.quantity.read(ref) if ref else None
                bound = self.bound.times * base if isinstance(base, float) else None
            else:
                bound = self.bound
            if actual is None or bound is None:
                ok = False
            elif isinstance(actual, str) or isinstance(bound, str):
                ok = (actual == bound) == (self.op == "==")
            else:
                ok = ops[self.op](actual, bound)
            checks.append(Check(tags=dict(row.tags), actual=actual, bound=bound, passed=ok))
        return Outcome(
            line=self.line,
            rule=self.raw,
            checks=checks,
            passed=all(c.passed for c in checks),
            error=None,
        )
