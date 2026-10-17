from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from bencher.contract.output.line import Line
from bencher.contract.output.outcome import Outcome
from bencher.contract.reason import Reason
from bencher.expect.rule import Rule
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
            if not cells or cells[0].lstrip().startswith("#"):
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
        return [rule.check(rows) for rule in self.rules.values()]

    def bless(self, rows: list[Line]) -> str:
        verdicts = {(line.name, self.canon(line.tags)): line for line in rows if line.verdict}
        covered: set[tuple[str, str]] = set()
        out: list[str] = []
        for number, raw in enumerate(self.lines, start=1):
            rule = self.rules.get(number)
            if rule is None or not rule.owned():
                out.append(raw)
                continue
            exact = (rule.name, self.canon(rule.tags))
            if exact in verdicts:
                covered.add(exact)
                out.append(self.blessed(verdicts[exact]))
            elif any(rule.matches(line) for line in rows):
                out.append(raw)
        if not self.lines:
            out.append("# " + Tsv.row(columns))
        out.extend(self.blessed(verdicts[exact]) for exact in sorted(set(verdicts) - covered))
        return "\n".join(out) + "\n"
