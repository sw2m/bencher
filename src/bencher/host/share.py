from __future__ import annotations

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

    def stage(self, root: Path, bench: Bench, runner: Runner, tree: Path, local: str) -> None:
        self.remove()
        self.path.mkdir(parents=True)
        with resources.as_file(resources.files("bencher").joinpath("states")) as states:
            shutil.copytree(states, self.path / "states")
        shutil.copytree(tree, self.path / "runner", symlinks=True)
        inputs = self.path / "in"
        (inputs / "artifacts").mkdir(parents=True)
        artifacts = {}
        for name, path in bench.artifacts.items():
            source = (root / path).resolve()
            target = inputs / "artifacts" / name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            artifacts[name] = f"{local}/in/artifacts/{name}"
        (inputs / "params.json").write_bytes(pydantic_core.to_json(bench.params) + b"\n")
        (inputs / "pins.json").write_bytes(pydantic_core.to_json(bench.pins) + b"\n")
        milliseconds = int(bench.telemetry.interval.total_seconds() * 1000)
        pillar: dict[str, Any] = {
            "bencher": {
                "params": bench.params,
                "pins": bench.pins,
                "artifacts": artifacts,
                "reporters": runner.reporters,
                "telemetry": {"interval": f"{milliseconds}ms"},
            }
        }
        (inputs / "pillar").mkdir()
        (inputs / "pillar" / "top.sls").write_text(yaml.safe_dump({"base": {"*": ["bencher"]}}))
        (inputs / "pillar" / "bencher.sls").write_text(yaml.safe_dump(pillar))
        self.publish()

    def publish(self) -> None:
        for path in [self.path, *self.path.rglob("*")]:
            if path.is_symlink():
                continue
            if path.is_dir():
                path.chmod(0o755)
            else:
                path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)

    def remove(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
