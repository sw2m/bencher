from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bencher.contract.bench import Bench
from bencher.contract.override import Override
from bencher.contract.runner import Runner
from bencher.expect import Rules
from bencher.host import fixture
from bencher.host.settings import Settings


class Halt(RuntimeError):
    pass


@dataclass(frozen=True)
class Plan:
    root: Path
    bench: Bench
    runner: Runner
    tree: Path
    rules: Rules | None
    overrides: list[Override]

    @classmethod
    def load(cls, root: Path, settings: Settings, overrides: list[Override]) -> Plan:
        bench = Bench.load(root, overrides)
        tree = fixture.fetch(bench.fixture, root, settings.cache)
        runner = Runner.load(tree)
        for name, path in bench.artifacts.items():
            if not (root / path).exists():
                raise Halt(f"artifact {name}: {root / path} does not exist")
        return cls(root, bench, runner, tree, Rules.load(root), overrides)
