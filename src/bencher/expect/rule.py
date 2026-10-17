from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Literal

from pydantic import Field, Json, StrictFloat, StrictInt, model_validator

from bencher.contract.model import Model
from bencher.contract.output.check import Check
from bencher.contract.output.line import Line
from bencher.contract.output.outcome import Outcome
from bencher.contract.output.row import Verdict
from bencher.expect.quantity import Quantity
from bencher.expect.relative import Relative

ops: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    ">": operator.gt,
}
Index = dict[tuple[str, tuple[tuple[str, str], ...]], Line]


class Rule(Model):
    line: int
    raw: str
    name: str = Field(min_length=1)
    tags: Json[dict[str, str]]
    quantity: Quantity
    op: Literal["<", "<=", "==", "!=", ">=", ">"]
    bound: Json[StrictInt | StrictFloat | Verdict | Relative]

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

    def threshold(self, row: Line, index: Index) -> tuple[float | str | None, str | None]:
        if not isinstance(self.bound, Relative):
            return self.bound, None
        tags = {**row.tags, **self.bound.of}
        if tags == row.tags:
            return None, f"{row.name} {dict(row.tags)} is its own reference"
        reference = index.get((row.name, tuple(sorted(tags.items()))))
        if reference is None:
            return None, f"no reference row {row.name} {tags}"
        base = self.quantity.read(reference)
        if not isinstance(base, float):
            return None, f"reference row {row.name} {tags} has no value for this quantity"
        return self.bound.times * base, None

    def compare(self, actual: float | str | None, bound: float | str | None) -> bool:
        if actual is None or bound is None:
            return False
        if isinstance(actual, str) or isinstance(bound, str):
            return (actual == bound) == (self.op == "==")
        return ops[self.op](actual, bound)

    def check(self, rows: list[Line]) -> Outcome:
        matched = [row for row in rows if self.matches(row)]
        if not matched:
            return Outcome(
                line=self.line, rule=self.raw, checks=[], passed=False, error="matches no row"
            )
        index = {row.key(): row for row in rows}
        checks = []
        problems = []
        for row in matched:
            actual = self.quantity.read(row)
            bound, problem = self.threshold(row, index)
            if problem:
                problems.append(problem)
            checks.append(
                Check(
                    tags=dict(row.tags),
                    actual=actual,
                    bound=bound,
                    passed=self.compare(actual, bound),
                )
            )
        return Outcome(
            line=self.line,
            rule=self.raw,
            checks=checks,
            passed=all(c.passed for c in checks),
            error="; ".join(problems) or None,
        )
