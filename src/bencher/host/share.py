from __future__ import annotations

import json
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from bencher.contract.bench import Bench
from bencher.contract.runner import Runner


def publish(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)


def stage(share: Path, root: Path, bench: Bench, runner: Runner, tree: Path, local: str) -> None:
    shutil.rmtree(share, ignore_errors=True)
    share.mkdir(parents=True)
    with resources.as_file(resources.files("bencher").joinpath("states")) as states:
        shutil.copytree(states, share / "states")
    shutil.copytree(tree, share / "runner", symlinks=True)
    inputs = share / "in"
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
    (inputs / "params.json").write_text(json.dumps(bench.params) + "\n")
    (inputs / "pins.json").write_text(json.dumps(bench.pins) + "\n")
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
    publish(share)
