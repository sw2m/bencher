from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from bencher import payload
from bencher.bench import Bench
from bencher.expect import Rules
from bencher.model import explain
from bencher.result import Report, Result
from bencher.run import Plan, Run, arch
from bencher.runner import Runner

schemas: dict[str, type[BaseModel]] = {
    "bench": Bench,
    "runner": Runner,
    "result": Result,
    "report": Report,
}


def run(args: argparse.Namespace) -> int:
    report = asyncio.run(Run(args.dir, keep=args.keep).start())
    text = report.dump()
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0 if report.passed else 1


def validate(args: argparse.Namespace) -> int:
    plan = Plan.load(args.dir.resolve())
    payload.components(plan.runner.os, arch())
    count = len(plan.rules.rules) if plan.rules else 0
    print(
        f"bencher: {args.dir}: ok ({plan.runner.os} {plan.runner.type}, {count} rules)",
        file=sys.stderr,
    )
    return 0


def bless(args: argparse.Namespace) -> int:
    report = Report.model_validate_json(Path(args.report).read_text())
    rules = Rules.load(args.dir) or Rules.parse("")
    (args.dir / "expect.tsv").write_text(rules.bless(report.rows))
    return 0


def schema(args: argparse.Namespace) -> int:
    model = schemas[args.kind]
    print(json.dumps(model.model_json_schema(by_alias=True), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bencher")
    verbs = parser.add_subparsers(dest="verb", required=True)
    p = verbs.add_parser("run", help="run a benchmark and write its report")
    p.add_argument("dir", type=Path)
    p.add_argument("--out", help="write the report here instead of stdout")
    p.add_argument("--keep", action="store_true", help="leave the instance running")
    p.set_defaults(handler=run)
    p = verbs.add_parser("validate", help="check bench.toml, the fixture runner and expect.tsv")
    p.add_argument("dir", type=Path)
    p.set_defaults(handler=validate)
    p = verbs.add_parser("bless", help="snapshot verdict rows of a report into expect.tsv")
    p.add_argument("dir", type=Path)
    p.add_argument("--report", required=True)
    p.set_defaults(handler=bless)
    p = verbs.add_parser("schema", help="print a JSON Schema")
    p.add_argument("kind", choices=sorted(schemas))
    p.set_defaults(handler=schema)
    args = parser.parse_args(argv)
    try:
        code: int = args.handler(args)
    except (OSError, ValueError, LookupError, RuntimeError) as error:
        print(f"bencher: {explain(error)}", file=sys.stderr)
        return 1
    return code
