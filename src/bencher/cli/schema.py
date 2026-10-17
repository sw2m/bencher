from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import CliPositionalArg

from bencher.cli.verb import Verb
from bencher.contract.input.bench import Bench
from bencher.contract.input.runner import Runner
from bencher.contract.output.report import Report
from bencher.contract.output.result import Result
from bencher.host.settings import Settings

contracts: dict[str, type[BaseModel]] = {
    "bench": Bench,
    "runner": Runner,
    "result": Result,
    "report": Report,
}


class Schema(Verb):
    kind: CliPositionalArg[Literal["bench", "runner", "result", "report"]] = Field(
        description="which contract"
    )

    def perform(self, settings: Settings) -> int:
        print(json.dumps(contracts[self.kind].model_json_schema(by_alias=True), indent=2))
        return 0
