from __future__ import annotations

import sys

from pydantic import Field
from pydantic_settings import CliApp, CliSubCommand, SettingsConfigDict, get_subcommand

from bencher.cli.bless import Bless
from bencher.cli.render import Render
from bencher.cli.run import Run
from bencher.cli.schema import Schema
from bencher.cli.validate import Validate
from bencher.cli.verb import Verb
from bencher.contract.reason import Reason
from bencher.host.settings import Settings


class Bencher(Settings):
    model_config = SettingsConfigDict(
        cli_prog_name="bencher",
        cli_kebab_case=True,
        cli_implicit_flags=True,
        cli_hide_none_type=True,
    )

    run: CliSubCommand[Run] = Field(description="run a benchmark and write its report")
    check: CliSubCommand[Validate] = Field(
        alias="validate", description="check bench.toml, the fixture runner and expect.tsv"
    )
    bless: CliSubCommand[Bless] = Field(description="snapshot a report's verdicts into expect.tsv")
    contract: CliSubCommand[Schema] = Field(alias="schema", description="print a JSON Schema")
    render: CliSubCommand[Render] = Field(description="print the composed cloud-config")

    @classmethod
    def main(cls) -> None:
        root = CliApp.run(cls, cli_args=sys.argv[1:])
        verb = get_subcommand(root)
        assert isinstance(verb, Verb)
        try:
            code = verb.perform(root)
        except (OSError, ValueError, LookupError, RuntimeError) as error:
            print(f"bencher: {Reason.of(error)}", file=sys.stderr)
            code = 1
        raise SystemExit(code)
