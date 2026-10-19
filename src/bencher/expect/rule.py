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
from bencher.expect.table import Key, Table

ops: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    ">": operator.gt,
}


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
        if self.quantity.kind == "rules" and isinstance(self.bound, Relative):
            raise ValueError("a rules quantity takes a number bound")
        return self

    def matches(self, row: Line) -> bool:
        return row.name == self.name and all(row.tags.get(k) == v for k, v in self.tags.items())

    def owned(self) -> bool:
        return self.quantity.kind == "verdict" and self.op == "=="

    def threshold(
        self, row: Line, table: Table, counts: dict[Key, int]
    ) -> tuple[float | str | None, str | None]:
        if not isinstance(self.bound, Relative):
            return self.bound, None
        tags = {**row.tags, **self.bound.of}
        if tags == row.tags:
            return None, f"{row.name} {dict(row.tags)} is its own reference"
        reference = table.get(row.name, tags)
        if reference is None:
            return None, f"no reference row {row.name} {tags}"
        base = self.quantity.read(reference, counts)
        if not isinstance(base, float):
            return None, f"reference row {row.name} {tags} has no value for this quantity"
        return self.bound.times * base, None

    def compare(self, actual: float | str | None, bound: float | str | None) -> bool:
        if actual is None or bound is None:
            return False
        if isinstance(actual, str) or isinstance(bound, str):
            return (actual == bound) == (self.op == "==")
        return ops[self.op](actual, bound)

    def check(self, table: Table, counts: dict[Key, int]) -> Outcome:
        matched = table.select(self.name, self.tags)
        if not matched:
            return Outcome(
                line=self.line, rule=self.raw, checks=[], passed=False, error="matches no row"
            )
        checks = []
        problems = []
        for row in matched:
            actual = self.quantity.read(row, counts)
            bound, problem = self.threshold(row, table, counts)
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
