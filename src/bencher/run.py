from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import sys
import uuid
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from bencher import cache, fixture, payload
from bencher.bench import Bench
from bencher.expect import Rules
from bencher.guest import Guest, find
from bencher.incus import Exec, Failure, Incus
from bencher.model import explain, seconds
from bencher.result import Error, Line, Report, Result, Stage
from bencher.runner import Runner
from bencher.stats import Stats
from bencher.telemetry import Series, fold, parse


class Halt(RuntimeError):
    pass


def arch() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)


def place(source: Traversable, target: Path) -> None:
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            place(child, target / child.name)
    else:
        target.write_bytes(source.read_bytes())


def publish(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)


@dataclass
class Plan:
    bench: Bench
    runner: Runner
    tree: Path
    guest: Guest
    rules: Rules | None

    @classmethod
    def load(cls, root: Path) -> Plan:
        bench = Bench.load(root)
        tree = fixture.fetch(bench.fixture, root, cache.root())
        runner = Runner.load(tree)
        guest = find(runner.os)
        entry = tree / "run"
        if not entry.is_file() or not os.access(entry, os.X_OK):
            raise Halt(f"{entry} is not an executable entrypoint")
        for name, path in bench.artifacts.items():
            if not (root / path).exists():
                raise Halt(f"artifact {name}: {root / path} does not exist")
        return cls(bench, runner, tree, guest, Rules.load(root))


class Run:
    def __init__(self, root: Path, keep: bool, log: TextIO = sys.stderr) -> None:
        self.root = root.resolve()
        self.keep = keep
        self.log = log
        self.name = f"bencher-{uuid.uuid4().hex[:12]}"
        self.errors: list[Error] = []

    def say(self, text: str) -> None:
        print(f"bencher: {text}", file=self.log, flush=True)

    def stage(self, plan: Plan, share: Path) -> None:
        shutil.rmtree(share, ignore_errors=True)
        share.mkdir(parents=True)
        files = plan.guest.files()
        (share / "bencher").mkdir()
        for child in files.iterdir():
            if child.name != "bootstrap.sh":
                place(child, share / "bencher" / child.name)
        (share / "bencher" / "minion").write_text(plan.guest.minion())
        place(resources.files("bencher").joinpath("states"), share / "states")
        shutil.copytree(plan.tree, share / "runner", symlinks=True)
        inputs = share / "in"
        (inputs / "artifacts").mkdir(parents=True)
        artifacts = {}
        for name, path in plan.bench.artifacts.items():
            source = (self.root / path).resolve()
            target = inputs / "artifacts" / name
            if source.is_dir():
                shutil.copytree(source, target, symlinks=False)
            else:
                shutil.copy2(source, target)
            artifacts[name] = f"{plan.guest.local}/in/artifacts/{name}"
        (inputs / "params.json").write_text(json.dumps(plan.bench.params) + "\n")
        (inputs / "pins.json").write_text(json.dumps(plan.bench.pins) + "\n")
        pillar = {
            "bencher": {
                "params": plan.bench.params,
                "pins": plan.bench.pins,
                "artifacts": artifacts,
                "reporters": plan.runner.reporters,
                "telemetry": {"interval": plan.bench.telemetry.interval},
            }
        }
        (inputs / "pillar").mkdir()
        (inputs / "pillar" / "top.sls").write_text("base:\n  '*':\n    - bencher\n")
        (inputs / "pillar" / "bencher.sls").write_text("#!json\n" + json.dumps(pillar) + "\n")
        publish(share)

    async def provision(self, incus: Incus, plan: Plan) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds(plan.bench.limits.provision)
        while loop.time() < deadline:
            try:
                done = await incus.exec(self.name, plan.guest.wait(), deadline - loop.time())
            except Failure:
                await asyncio.sleep(2)
                continue
            state = done.stdout.decode(errors="replace").strip()
            if state == "ok":
                return
            if state == "failed":
                await self.dump(incus, plan)
                raise Halt("provisioning failed; see the provision log above")
            await asyncio.sleep(2)
        await self.dump(incus, plan)
        raise Halt(f"provisioning did not finish within {plan.bench.limits.provision}")

    async def dump(self, incus: Incus, plan: Plan) -> None:
        try:
            done = await incus.exec(self.name, plan.guest.log(), 60)
        except Failure as error:
            self.say(f"provision log unavailable: {error}")
            return
        self.log.write(done.stdout.decode(errors="replace"))

    async def execute(self, incus: Incus, plan: Plan) -> Result:
        limit = seconds(plan.bench.limits.run)
        done: Exec = await incus.exec(self.name, plan.guest.entry(limit), limit + 120)
        self.log.write(done.stderr.decode(errors="replace"))
        if done.code != 0:
            raise Halt(f"entrypoint exited {done.code}")
        try:
            result = Result.parse(done.stdout.decode())
        except (ValidationError, UnicodeDecodeError) as error:
            raise Halt(f"entrypoint stdout is not a result: {explain(error)}") from None
        declared = set(plan.runner.reporters)
        for row in result.rows:
            if row.reporter is not None and row.reporter not in declared:
                raise Halt(f"row {row.name} {dict(row.tags)}: undeclared reporter {row.reporter!r}")
        return result

    async def harvest(self, incus: Incus, plan: Plan) -> dict[str, dict[str, Series]]:
        done = await incus.exec(self.name, plan.guest.harvest(plan.runner.reporters), 600)
        if done.code != 0:
            self.log.write(done.stderr.decode(errors="replace"))
            raise Halt(f"harvest exited {done.code}")
        by, strays = parse(done.stdout.decode())
        for stray in sorted(set(strays)):
            self.errors.append(
                Error(stage="harvest", message=f"series without a declared reporter: {stray}")
            )
        return {reporter: fold(lines) for reporter, lines in by.items()}

    def report(self, plan: Plan, result: Result, telemetry: dict[str, dict[str, Series]]) -> Report:
        lines = [
            Line(
                **row.model_dump(),
                stats={unit: Stats.of(v) for unit, v in row.samples.items()}
                if row.samples
                else None,
                telemetry=telemetry.get(row.reporter, {}) if row.reporter else None,
            )
            for row in result.rows
        ]
        outcomes = plan.rules.check(lines) if plan.rules else []
        passed = not self.errors and all(o.passed for o in outcomes)
        return Report(
            passed=passed,
            rows=lines,
            system=telemetry.get("system", {}),
            expectations=outcomes,
            errors=self.errors,
        )

    def fail(self, stage: Stage, error: BaseException) -> Report:
        message = explain(error)
        self.say(f"{stage}: {message}")
        self.errors.append(Error(stage=stage, message=message))
        return Report(passed=False, errors=self.errors)

    async def start(self) -> Report:
        stage: Stage = "load"
        try:
            plan = Plan.load(self.root)
            stage = "payload"
            self.say(f"payload for {plan.runner.os}/{arch()}")
            bundle = payload.prepare(plan.runner.os, arch(), cache.root())
            stage = "launch"
            incus = Incus()
        except Exception as error:
            return self.fail(stage, error)
        share = cache.root() / "runs" / self.name
        try:
            self.stage(plan, share)
        except Exception as error:
            shutil.rmtree(share, ignore_errors=True)
            await incus.close()
            return self.fail("stage", error)
        try:
            self.say(f"launch {self.name}: {plan.runner.type} {plan.runner.image}")
            await incus.create(
                self.name,
                plan.runner.type,
                plan.runner.image,
                config={
                    "limits.cpu": str(plan.bench.limits.cpu),
                    "limits.memory": plan.bench.limits.memory,
                    "cloud-init.user-data": plan.guest.userdata(),
                },
                devices={
                    "payload": {
                        "type": "disk",
                        "source": str(bundle),
                        "path": "/bencher/payload",
                        "readonly": "true",
                    },
                    "run": {
                        "type": "disk",
                        "source": str(share),
                        "path": "/bencher/run",
                        "readonly": "true",
                    },
                },
                timeout=seconds(plan.bench.limits.provision),
            )
            stage = "provision"
            self.say("provision")
            await self.provision(incus, plan)
            stage = "execute"
            self.say("execute")
            result = await self.execute(incus, plan)
            stage = "harvest"
            self.say("harvest")
            telemetry = await self.harvest(incus, plan)
            stage = "report"
            return self.report(plan, result, telemetry)
        except Exception as error:
            return self.fail(stage, error)
        finally:
            await self.teardown(incus, share)

    async def teardown(self, incus: Incus, share: Path) -> None:
        if self.keep:
            self.say(f"kept {self.name}; share at {share}")
        else:
            try:
                await incus.delete(self.name)
            except Failure as error:
                self.say(f"teardown: {error}")
            shutil.rmtree(share, ignore_errors=True)
        await incus.close()
