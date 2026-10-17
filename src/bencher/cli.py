from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from bencher import run as lifecycle
from bencher.contract import explain
from bencher.contract.bench import Bench
from bencher.contract.override import Override
from bencher.contract.report import Report
from bencher.contract.result import Result
from bencher.contract.runner import Runner
from bencher.expect import Rules
from bencher.host.cloud import Target, targets
from bencher.host.settings import Settings
from bencher.run.plan import Plan

schemas: dict[str, type[BaseModel]] = {
    "bench": Bench,
    "runner": Runner,
    "result": Result,
    "report": Report,
}


def settings(args: argparse.Namespace) -> Settings:
    flags = {name: getattr(args, name, None) for name in ("socket", "cache", "keep", "out")}
    return Settings.layered(flags)


def overrides(args: argparse.Namespace) -> list[Override]:
    return [Override.parse(text) for text in args.set]


def run(args: argparse.Namespace) -> int:
    chosen = settings(args)
    report = asyncio.run(lifecycle.start(args.dir, chosen, overrides(args)))
    text = report.dump()
    if chosen.out:
        chosen.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0 if report.passed else 1


def validate(args: argparse.Namespace) -> int:
    plan = Plan.load(args.dir.resolve(), settings(args), overrides(args))
    count = len(plan.rules.rules) if plan.rules else 0
    print(f"bencher: {args.dir}: ok ({plan.runner.type}, {count} rules)", file=sys.stderr)
    return 0


def bless(args: argparse.Namespace) -> int:
    report = Report.model_validate_json(Path(args.report).read_text())
    rules = Rules.load(args.dir) or Rules.parse("")
    (args.dir / "expect.tsv").write_text(rules.bless(report.rows))
    return 0


def schema(args: argparse.Namespace) -> int:
    print(json.dumps(schemas[args.kind].model_json_schema(by_alias=True), indent=2))
    return 0


def render(args: argparse.Namespace) -> int:
    if args.all:
        args.all.mkdir(parents=True, exist_ok=True)
        for target in targets():
            path = args.all / f"{target.distro}.{target.release}.{target.kind}.yaml"
            path.write_text(target.compose())
            print(path, file=sys.stderr)
        return 0
    if not (args.distro and args.release):
        raise ValueError("render needs <distro> <release>, or --all <dir>")
    sys.stdout.write(Target(args.type, args.distro, args.release).compose())
    return 0


def main(argv: list[str] | None = None) -> int:
    operator = argparse.ArgumentParser(add_help=False)
    operator.add_argument("--socket", help="Incus or LXD unix socket")
    operator.add_argument("--cache", type=Path, help="fixture and run cache")
    operator.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a bench.toml value (recorded in the report)",
    )
    parser = argparse.ArgumentParser(prog="bencher")
    verbs = parser.add_subparsers(dest="verb", required=True)
    p = verbs.add_parser("run", parents=[operator], help="run a benchmark and write its report")
    p.add_argument("dir", type=Path)
    p.add_argument("--out", type=Path, help="write the report here instead of stdout")
    p.add_argument("--keep", action="store_true", default=None, help="leave the instance running")
    p.set_defaults(handler=run)
    p = verbs.add_parser(
        "validate", parents=[operator], help="check bench.toml, the fixture runner and expect.tsv"
    )
    p.add_argument("dir", type=Path)
    p.set_defaults(handler=validate)
    p = verbs.add_parser("bless", help="snapshot verdict rows of a report into expect.tsv")
    p.add_argument("dir", type=Path)
    p.add_argument("--report", required=True)
    p.set_defaults(handler=bless)
    p = verbs.add_parser("schema", help="print a JSON Schema")
    p.add_argument("kind", choices=sorted(schemas))
    p.set_defaults(handler=schema)
    p = verbs.add_parser("render", help="print the composed cloud-config for a guest")
    p.add_argument("distro", nargs="?")
    p.add_argument("release", nargs="?")
    p.add_argument("--type", choices=["container", "vm"], default="container")
    p.add_argument("--all", type=Path, metavar="DIR", help="render every target into DIR")
    p.set_defaults(handler=render)
    args = parser.parse_args(argv)
    try:
        code: int = args.handler(args)
    except (OSError, ValueError, LookupError, RuntimeError) as error:
        print(f"bencher: {explain(error)}", file=sys.stderr)
        return 1
    return code
