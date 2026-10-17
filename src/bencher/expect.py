from __future__ import annotations

import json
import operator
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bencher.result import Check, Line, Outcome

ops: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    ">": operator.gt,
}
fields = {
    "samples": {"n", "min", "max", "mean", "median", "stdev"},
    "telemetry": {"n", "min", "max", "mean", "median", "first", "last", "delta"},
}
header = "# name\ttags\tquantity\top\tbound\n"


def canon(tags: dict[str, str]) -> str:
    return json.dumps(tags, sort_keys=True, ensure_ascii=False)


def tagset(text: str, line: int) -> dict[str, str]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"expect.tsv:{line}: tags are not JSON: {error}") from None
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ValueError(f"expect.tsv:{line}: tags must be a JSON object of strings")
    return value


@dataclass(frozen=True)
class Quantity:
    kind: str
    key: str
    stat: str

    @classmethod
    def parse(cls, text: str, line: int) -> Quantity:
        if text == "verdict":
            return cls("verdict", "", "")
        kind, _, rest = text.partition(".")
        key, _, stat = rest.rpartition(".")
        if kind not in fields or not key or stat not in fields[kind]:
            raise ValueError(f"expect.tsv:{line}: bad quantity {text!r}")
        return cls(kind, key, stat)

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


@dataclass(frozen=True)
class Relative:
    factor: float
    tags: dict[str, str]


@dataclass(frozen=True)
class Rule:
    line: int
    raw: str
    name: str
    tags: dict[str, str]
    quantity: Quantity
    op: str
    bound: float | str | Relative

    @classmethod
    def parse(cls, raw: str, line: int) -> Rule:
        cells = raw.split("\t")
        if len(cells) != 5:
            raise ValueError(f"expect.tsv:{line}: expected 5 tab-separated cells, got {len(cells)}")
        name, tags, quantity, op, bound = (c.strip() for c in cells)
        if op not in ops:
            raise ValueError(f"expect.tsv:{line}: bad op {op!r}")
        q = Quantity.parse(quantity, line)
        parsed: float | str | Relative
        if q.kind == "verdict":
            if bound not in ("ok", "err") or op not in ("==", "!="):
                raise ValueError(f"expect.tsv:{line}: a verdict rule is '== ok|err' or '!= ok|err'")
            parsed = bound
        elif match := re.fullmatch(r"([0-9.eE+-]+)\s*\*\s*(\{.*\})", bound):
            parsed = Relative(float(match.group(1)), tagset(match.group(2), line))
        else:
            try:
                parsed = float(bound)
            except ValueError:
                raise ValueError(f"expect.tsv:{line}: bad bound {bound!r}") from None
        return cls(line, raw, name, tagset(tags, line), q, op, parsed)

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
                tags = {**row.tags, **self.bound.tags}
                ref = index.get((row.name, tuple(sorted(tags.items()))))
                base = self.quantity.read(ref) if ref else None
                bound = self.bound.factor * base if isinstance(base, float) else None
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


@dataclass(frozen=True)
class Rules:
    lines: list[str]
    rules: dict[int, Rule]

    @classmethod
    def parse(cls, text: str) -> Rules:
        lines = text.splitlines()
        rules = {}
        for number, raw in enumerate(lines, start=1):
            if raw.strip() and not raw.lstrip().startswith("#"):
                rules[number] = Rule.parse(raw, number)
        return cls(lines, rules)

    @classmethod
    def load(cls, root: Path) -> Rules | None:
        path = root / "expect.tsv"
        return cls.parse(path.read_text()) if path.exists() else None

    def check(self, rows: list[Line]) -> list[Outcome]:
        return [rule.check(rows) for rule in self.rules.values()]

    def bless(self, rows: list[Line]) -> str:
        verdicts = {(row.name, canon(row.tags)): row for row in rows if row.verdict}
        covered: set[tuple[str, str]] = set()
        out: list[str] = []
        for number, raw in enumerate(self.lines, start=1):
            rule = self.rules.get(number)
            if rule is None or not rule.owned():
                out.append(raw)
                continue
            exact = (rule.name, canon(rule.tags))
            if exact in verdicts:
                covered.add(exact)
                out.append(blessed(verdicts[exact]))
            elif any(rule.matches(row) for row in rows):
                out.append(raw)
        if not self.lines:
            out.append(header.rstrip("\n"))
        for exact in sorted(set(verdicts) - covered):
            out.append(blessed(verdicts[exact]))
        return "\n".join(out) + "\n"


def blessed(row: Line) -> str:
    return "\t".join([row.name, canon(row.tags), "verdict", "==", str(row.verdict)])
