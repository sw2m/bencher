from __future__ import annotations

import asyncio
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import TextIO

import httpx
from pydantic import ValidationError

from bencher.contract import explain
from bencher.contract.override import Override
from bencher.contract.report import Error, Line, Report, Stage
from bencher.contract.result import Result
from bencher.guest import Guest, find
from bencher.host import share
from bencher.host.cloud import Target
from bencher.host.incus import Failure, Incus
from bencher.host.settings import Settings
from bencher.measure.stats import Stats
from bencher.measure.telemetry import Series, split
from bencher.run.plan import Halt, Plan


class Run:
    def __init__(self, plan: Plan, settings: Settings, log: TextIO = sys.stderr) -> None:
        self.plan = plan
        self.settings = settings
        self.log = log
        self.name = f"bencher-{uuid.uuid4().hex[:12]}"
        self.share = settings.cache / "runs" / self.name
        self.errors: list[Error] = []

    def say(self, text: str) -> None:
        print(f"bencher: {text}", file=self.log, flush=True)

    def fail(self, stage: Stage, error: BaseException) -> None:
        message = explain(error)
        self.say(f"{stage}: {message}")
        self.errors.append(Error(stage=stage, message=message))

    def overrides(self) -> dict[str, object]:
        return {override.path: override.value for override in self.plan.overrides}

    async def create(self, incus: Incus) -> tuple[Guest, Target]:
        plan = self.plan
        self.say(f"launch {self.name}: {plan.runner.type} {plan.runner.image}")
        self.share.mkdir(parents=True, exist_ok=True)
        await incus.create(
            self.name,
            plan.runner.type,
            plan.runner.image,
            config={
                "limits.cpu": str(plan.bench.limits.cpu),
                "limits.memory": str(int(plan.bench.limits.memory)),
            },
            devices={
                "run": {
                    "type": "disk",
                    "source": str(self.share),
                    "path": "/bencher/run",
                    "readonly": "true",
                },
            },
            timeout=plan.bench.limits.provision.total_seconds(),
        )
        target = Target.of(plan.runner.type, await incus.config(self.name))
        guest = find(target.family())
        entry = plan.tree / guest.program
        if not entry.is_file() or not os.access(entry, os.X_OK):
            raise Halt(f"{entry} is not an executable entrypoint")
        return guest, target

    async def boot(self, incus: Incus, target: Target) -> None:
        self.say(f"cloud-init for {target.distro} {target.release}")
        await incus.configure(self.name, {"cloud-init.user-data": target.compose()})
        await incus.state(self.name, "start", 300)

    async def provision(self, incus: Incus, guest: Guest) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.plan.bench.limits.provision.total_seconds()
        while loop.time() < deadline:
            try:
                done = await incus.exec(self.name, guest.wait(), deadline - loop.time())
            except Failure:
                await asyncio.sleep(2)
                continue
            if done.code == 0:
                return
            if done.code == 1:
                await self.dump(incus, guest)
                raise Halt("cloud-init reported an error; see its output above")
            await asyncio.sleep(2)
        await self.dump(incus, guest)
        raise Halt(f"provisioning did not finish within {self.plan.bench.limits.provision}")

    async def dump(self, incus: Incus, guest: Guest) -> None:
        try:
            done = await incus.exec(self.name, guest.log(), 60)
        except Failure as error:
            self.say(f"cloud-init output unavailable: {error}")
            return
        self.log.write(done.stdout.decode(errors="replace"))

    async def execute(self, incus: Incus, guest: Guest) -> Result:
        limit = self.plan.bench.limits.run
        done = await incus.exec(self.name, guest.run(limit), limit.total_seconds() + 120)
        self.log.write(done.stderr.decode(errors="replace"))
        if done.code != 0:
            raise Halt(f"entrypoint exited {done.code}")
        try:
            result = Result.parse(done.stdout.decode())
        except (ValidationError, UnicodeDecodeError) as error:
            raise Halt(f"entrypoint stdout is not a result: {explain(error)}") from None
        declared = set(self.plan.runner.reporters)
        for row in result.rows:
            if row.reporter is not None and row.reporter not in declared:
                raise Halt(f"row {row.name} {dict(row.tags)}: undeclared reporter {row.reporter!r}")
        return result

    async def harvest(self, incus: Incus, guest: Guest) -> dict[str, dict[str, Series]]:
        done = await incus.exec(self.name, guest.flush(), 120)
        if done.code != 0:
            raise Halt(f"stopping the collector exited {done.code}")
        base = f"http://{await incus.address(self.name)}:8428"
        async with httpx.AsyncClient(base_url=base, timeout=120) as client:
            (await client.get("/internal/force_flush")).raise_for_status()
            response = await client.get("/api/v1/export", params={"match[]": '{__name__!=""}'})
            response.raise_for_status()
        telemetry, strays = split(response.text, {*self.plan.runner.reporters, "system"})
        for stray in strays:
            self.errors.append(
                Error(stage="harvest", message=f"series without a declared reporter: {stray}")
            )
        return telemetry

    def report(self, result: Result, telemetry: dict[str, dict[str, Series]]) -> Report:
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
        outcomes = self.plan.rules.check(lines) if self.plan.rules else []
        return Report(
            passed=not self.errors and all(o.passed for o in outcomes),
            overrides=self.overrides(),
            rows=lines,
            system=telemetry.get("system", {}),
            expectations=outcomes,
            errors=self.errors,
        )

    def failed(self) -> Report:
        return Report(passed=False, overrides=self.overrides(), errors=self.errors)

    async def start(self) -> Report:
        plan = self.plan
        try:
            incus = Incus(self.settings.socket)
        except Exception as error:
            self.fail("launch", error)
            return self.failed()
        stage: Stage = "launch"
        try:
            guest, target = await self.create(incus)
            stage = "stage"
            share.stage(self.share, plan.root, plan.bench, plan.runner, plan.tree, guest.local)
            stage = "launch"
            await self.boot(incus, target)
            stage = "provision"
            self.say("provision")
            await self.provision(incus, guest)
            stage = "execute"
            self.say("execute")
            result = await self.execute(incus, guest)
            stage = "harvest"
            self.say("harvest")
            telemetry = await self.harvest(incus, guest)
            stage = "report"
            return self.report(result, telemetry)
        except Exception as error:
            self.fail(stage, error)
            return self.failed()
        finally:
            await self.teardown(incus)

    async def teardown(self, incus: Incus) -> None:
        if self.settings.keep:
            self.say(f"kept {self.name}; share at {self.share}")
        else:
            try:
                await incus.delete(self.name)
            except Failure as error:
                self.say(f"teardown: {error}")
            shutil.rmtree(self.share, ignore_errors=True)
        await incus.close()


async def start(root: Path, settings: Settings, overrides: list[Override]) -> Report:
    try:
        plan = Plan.load(root.resolve(), settings, overrides)
    except Exception as error:
        message = explain(error)
        print(f"bencher: load: {message}", file=sys.stderr, flush=True)
        values = {o.path: o.value for o in overrides}
        return Report(passed=False, overrides=values, errors=[Error(stage="load", message=message)])
    return await Run(plan, settings).start()
