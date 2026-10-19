from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

import pydantic_core
import yaml

from bencher.contract.input.bench import Bench
from bencher.contract.input.runner import Runner


class Share:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.inputs = path / "in"

    def stage(self, root: Path, bench: Bench, runner: Runner, tree: Path) -> None:
        self.remove()
        self.path.mkdir(parents=True)
        self.states()
        self.runner(tree, (root / bench.fixture.path).resolve())
        artifacts = self.artifacts(root, bench)
        self.params(bench)
        self.pillar(bench, runner, artifacts)
        self.write()
        self.publish()

    def states(self) -> None:
        with resources.as_file(resources.files("bencher").joinpath("states")) as states:
            shutil.copytree(states, self.path / "states")

    def runner(self, tree: Path, fixture: Path) -> None:
        for link in tree.rglob("*"):
            if not link.is_symlink():
                continue
            target = link.resolve()
            if not target.exists():
                raise FileNotFoundError(f"runner symlink {link} is dangling")
            if not target.is_relative_to(fixture):
                raise PermissionError(f"runner symlink {link} leaves the fixture: {target}")
        shutil.copytree(tree, self.path / "runner")

    def artifacts(self, root: Path, bench: Bench) -> dict[str, str]:
        (self.inputs / "artifacts").mkdir(parents=True)
        placed = {}
        for name, path in bench.artifacts.items():
            source = (root / path).resolve()
            target = self.inputs / "artifacts" / name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            placed[name] = "{{ grains.bencher.inputs }}/artifacts/" + name
        return placed

    def params(self, bench: Bench) -> None:
        (self.inputs / "params.json").write_bytes(pydantic_core.to_json(bench.params) + b"\n")

    def pillar(self, bench: Bench, runner: Runner, artifacts: dict[str, str]) -> None:
        seconds = bench.telemetry.interval.total_seconds()
        ready = max(60, int(3 * seconds) + 1)  # the first gather lands within 2 intervals
        self.data: dict[str, Any] = {
            "bencher": {
                "params": bench.params,
                "artifacts": artifacts,
                "reporters": runner.reporters,
                "telemetry": {"interval": f"{int(seconds * 1000)}ms", "ready": ready},
                "phase": "run",
                "resolved": {},
            }
        }

    def write(self) -> None:
        pillar = self.inputs / "pillar"
        pillar.mkdir(exist_ok=True)
        (pillar / "top.sls").write_text(yaml.safe_dump({"base": {"*": ["bencher"]}}))
        (pillar / "bencher.sls").write_text(yaml.safe_dump(self.data))
        resolved = self.data["bencher"]["resolved"]
        (self.inputs / "resolved.json").write_bytes(pydantic_core.to_json(resolved) + b"\n")

    @property
    def resolver(self) -> bool:
        return os.access(self.path / "runner" / "resolve", os.X_OK)

    def resolve(self, resolved: dict[str, Any]) -> None:
        self.data["bencher"]["resolved"] = resolved
        self.write()
        self.publish()

    def publish(self) -> None:
        for path in [self.path, *self.path.rglob("*")]:
            if path.is_dir():
                path.chmod(0o755)
            else:
                path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)

    def device(self) -> dict[str, str]:
        return {
            "type": "disk",
            "source": str(self.path),
            "path": "/bencher/run",
            "readonly": "true",
        }

    def remove(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
