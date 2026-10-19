from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from bencher.contract.output.line import Line
from bencher.contract.output.outcome import Outcome
from bencher.contract.reason import Reason
from bencher.expect.rule import Rule
from bencher.expect.table import Table
from bencher.expect.tsv import Tsv

columns = ["name", "tags", "quantity", "op", "bound"]


@dataclass(frozen=True)
class Rules:
    lines: list[str]
    rules: dict[int, Rule]

    @staticmethod
    def canon(tags: dict[str, str]) -> str:
        return json.dumps(tags, sort_keys=True, ensure_ascii=False)

    @classmethod
    def blessed(cls, line: Line) -> str:
        return Tsv.row([line.name, cls.canon(line.tags), "verdict", "==", json.dumps(line.verdict)])

    @classmethod
    def parse(cls, text: str) -> Rules:
        lines = text.splitlines()
        rules = {}
        for number, cells in Tsv.rows(text):
            if not "".join(cells).strip() or cells[0].lstrip().startswith("#"):
                continue
            if len(cells) != len(columns):
                raise ValueError(f"expect.tsv:{number}: expected {len(columns)} cells")
            fields = dict(zip(columns, cells, strict=True))
            try:
                rules[number] = Rule.model_validate(
                    {"line": number, "raw": lines[number - 1], **fields}
                )
            except ValidationError as error:
                raise ValueError(f"expect.tsv:{number}: {Reason.of(error)}") from None
        return cls(lines, rules)

    @classmethod
    def load(cls, root: Path) -> Rules | None:
        path = root / "expect.tsv"
        return cls.parse(path.read_text()) if path.exists() else None

    def check(self, rows: list[Line]) -> list[Outcome]:
        table = Table(rows)
        counted = any(rule.quantity.kind == "rules" for rule in self.rules.values())
        exact = (
            table.get(rule.name, rule.tags)
            for rule in self.rules.values()
            if counted and rule.quantity.kind != "rules"
        )
        counts = Counter(row.key() for row in exact if row is not None)
        return [rule.check(table, counts) for rule in self.rules.values()]

    def bless(self, rows: list[Line]) -> tuple[str, list[str]]:
        verdicts = {(line.name, self.canon(line.tags)): line for line in rows if line.verdict}
        coverage = [rule for rule in self.rules.values() if rule.quantity.kind == "rules"]
        covered: set[tuple[str, str]] = set()
        changes: list[str] = []
        out: list[str] = []
        for number, raw in enumerate(self.lines, start=1):
            rule = self.rules.get(number)
            if rule is None or not rule.owned():
                out.append(raw)
                continue
            exact = (rule.name, self.canon(rule.tags))
            if exact in verdicts:
                covered.add(exact)
                fresh = self.blessed(verdicts[exact])
                if fresh != raw:
                    changes.append(f"updated line {number}: {fresh}")
                out.append(fresh)
                continue
            matched = [line for line in rows if line.verdict and rule.matches(line)]
            if not matched:
                changes.append(f"dropped line {number}, it matches no row: {raw}")
                continue
            out.append(raw)
            for line in matched:
                if line.verdict == rule.bound:
                    if not any(c.matches(line) for c in coverage):
                        covered.add((line.name, self.canon(line.tags)))
                else:
                    changes.append(
                        f"kept line {number}, but {line.name} {self.canon(line.tags)} is now"
                        f" {json.dumps(line.verdict)}: {raw}"
                    )
        if not self.lines:
            out.append("# " + Tsv.row(columns))
        for exact in sorted(set(verdicts) - covered):
            fresh = self.blessed(verdicts[exact])
            changes.append(f"added: {fresh}")
            out.append(fresh)
        return "\n".join(out) + "\n", changes
