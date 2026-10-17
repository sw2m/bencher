from __future__ import annotations

import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import CliPositionalArg

from bencher.cli.verb import Verb
from bencher.contract.input.override import Override
from bencher.host.settings import Settings
from bencher.lifecycle.plan import Plan


class Lint(Verb):
    dir: CliPositionalArg[Path] = Field(description="bench directory holding bench.toml")
    overrides: list[str] = Field(
        default=[], alias="set", description="KEY=VALUE override of bench.toml, repeatable"
    )

    def perform(self, settings: Settings) -> int:
        overrides = [Override.parse(text) for text in self.overrides]
        plan = Plan.load(self.dir.resolve(), settings.cache, overrides)
        count = len(plan.rules.rules) if plan.rules else 0
        print(f"bencher: {self.dir}: ok ({plan.runner.type}, {count} rules)", file=sys.stderr)
        return 0
