from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bencher.contract.input.bench import Bench
from bencher.contract.input.override import Override
from bencher.contract.input.resolved import Resolved
from bencher.contract.input.runner import Runner
from bencher.expect.rules import Rules
from bencher.lifecycle.halt import Halt


@dataclass(frozen=True)
class Plan:
    root: Path
    bench: Bench
    runner: Runner
    tree: Path
    rules: Rules | None
    overrides: list[Override]
    resolved: dict[str, Any]

    @classmethod
    def load(cls, root: Path, overrides: list[Override]) -> Plan:
        bench = Bench.load(root, overrides)
        tree = bench.fixture.locate(root)
        runner = Runner.load(tree)
        for name, path in bench.artifacts.items():
            if not (root / path).exists():
                raise Halt(f"artifact {name}: {root / path} does not exist")
        resolved = Resolved.load(root / bench.resolved) if bench.resolved is not None else {}
        return cls(root, bench, runner, tree, Rules.load(root), overrides, resolved)
