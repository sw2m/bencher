from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from bencher.contract import explain
from bencher.contract.report import Line, Outcome
from bencher.expect.rule import Rule

columns = ["name", "tags", "quantity", "op", "bound"]


class Tsv(csv.Dialect):
    delimiter = "\t"
    quoting = csv.QUOTE_NONE
    quotechar = None
    escapechar = None
    doublequote = False
    skipinitialspace = False
    lineterminator = ""
    strict = True


def canon(tags: dict[str, str]) -> str:
    return json.dumps(tags, sort_keys=True, ensure_ascii=False)


def row(cells: list[str]) -> str:
    out = io.StringIO()
    csv.writer(out, dialect=Tsv).writerow(cells)
    return out.getvalue()


def blessed(line: Line) -> str:
    return row([line.name, canon(line.tags), "verdict", "==", json.dumps(line.verdict)])


@dataclass(frozen=True)
class Rules:
    lines: list[str]
    rules: dict[int, Rule]

    @classmethod
    def parse(cls, text: str) -> Rules:
        lines = text.splitlines()
        rules = {}
        reader = csv.reader(io.StringIO(text), dialect=Tsv)
        for cells in reader:
            number = reader.line_num
            if not cells or cells[0].lstrip().startswith("#"):
                continue
            if len(cells) != len(columns):
                raise ValueError(f"expect.tsv:{number}: expected {len(columns)} cells")
            try:
                rules[number] = Rule.model_validate(
                    {
                        "line": number,
                        "raw": lines[number - 1],
                        **dict(zip(columns, cells, strict=True)),
                    }
                )
            except ValidationError as error:
                raise ValueError(f"expect.tsv:{number}: {explain(error)}") from None
        return cls(lines, rules)

    @classmethod
    def load(cls, root: Path) -> Rules | None:
        path = root / "expect.tsv"
        return cls.parse(path.read_text()) if path.exists() else None

    def check(self, rows: list[Line]) -> list[Outcome]:
        return [rule.check(rows) for rule in self.rules.values()]

    def bless(self, rows: list[Line]) -> str:
        verdicts = {(line.name, canon(line.tags)): line for line in rows if line.verdict}
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
            elif any(rule.matches(line) for line in rows):
                out.append(raw)
        if not self.lines:
            out.append("# " + row(columns))
        out.extend(blessed(verdicts[exact]) for exact in sorted(set(verdicts) - covered))
        return "\n".join(out) + "\n"
