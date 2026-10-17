from __future__ import annotations

import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import CliPositionalArg

from bencher.cli.verb import Verb
from bencher.contract.output.report import Report
from bencher.expect.rules import Rules
from bencher.host.settings import Settings


class Bless(Verb):
    dir: CliPositionalArg[Path] = Field(description="bench directory whose expect.tsv to rewrite")
    report: Path = Field(description="the report to snapshot verdicts from")

    def perform(self, settings: Settings) -> int:
        report = Report.model_validate_json(self.report.read_text())
        if report.errors:
            stages = ", ".join(sorted({error.stage for error in report.errors}))
            raise ValueError(f"{self.report} failed at {stages}; bless only a completed run")
        rules = Rules.load(self.dir) or Rules.parse("")
        text, changes = rules.bless(report.rows)
        (self.dir / "expect.tsv").write_text(text)
        for change in changes:
            print(f"bencher: {change}", file=sys.stderr)
        return 0
