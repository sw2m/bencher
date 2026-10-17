from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import CliPositionalArg

from bencher import lifecycle
from bencher.cli.verb import Verb
from bencher.contract.input.override import Override
from bencher.host.settings import Settings


class Run(Verb):
    dir: CliPositionalArg[Path] = Field(description="bench directory holding bench.toml")
    out: Path | None = Field(default=None, description="write the report here instead of stdout")
    keep: bool = Field(default=False, description="leave the instance running")
    overrides: list[str] = Field(
        default=[], alias="set", description="KEY=VALUE override of bench.toml, repeatable"
    )

    def perform(self, settings: Settings) -> int:
        overrides = [Override.parse(text) for text in self.overrides]
        report = asyncio.run(lifecycle.Run.perform(self.dir, settings, self.keep, overrides))
        text = report.dump()
        if self.out:
            self.out.write_text(text)
        else:
            sys.stdout.write(text)
        return 0 if report.passed else 1
