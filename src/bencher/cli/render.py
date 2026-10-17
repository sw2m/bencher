from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import CliPositionalArg

from bencher.cli.verb import Verb
from bencher.host.settings import Settings
from bencher.host.target import Target


class Render(Verb):
    distro: CliPositionalArg[str | None] = Field(default=None, description="e.g. debian")
    release: CliPositionalArg[str | None] = Field(default=None, description="e.g. bookworm")
    type: Literal["container", "vm"] = Field(default="container", description="instance type")
    every: Path | None = Field(default=None, alias="all", description="render every target here")

    def perform(self, settings: Settings) -> int:
        if self.every:
            self.every.mkdir(parents=True, exist_ok=True)
            for target in Target.every():
                path = self.every / f"{target.distro}.{target.release}.{target.kind}.yaml"
                path.write_text(target.compose())
                print(path, file=sys.stderr)
            return 0
        if not (self.distro and self.release):
            raise ValueError("render needs <distro> <release>, or --all <dir>")
        sys.stdout.write(Target(self.type, self.distro, self.release).compose())
        return 0
