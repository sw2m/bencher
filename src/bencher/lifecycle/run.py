from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import TextIO

import httpx
from pydantic import ValidationError

from bencher.contract.input.override import Override
from bencher.contract.output.error import Error
from bencher.contract.output.line import Line
from bencher.contract.output.report import Report
from bencher.contract.output.result import Result
from bencher.contract.output.stage import Stage
from bencher.contract.reason import Reason
from bencher.guest.guest import Guest
from bencher.host.failure import Failure
from bencher.host.incus import Incus
from bencher.host.settings import Settings
from bencher.host.share import Share
from bencher.host.target import Target
from bencher.lifecycle.halt import Halt
from bencher.lifecycle.plan import Plan
from bencher.measure.export import Export
from bencher.measure.series import Series
from bencher.measure.stats import Stats


class Run:
    def __init__(
        self, plan: Plan, settings: Settings, keep: bool, log: TextIO = sys.stderr
    ) -> None:
        self.plan = plan
        self.settings = settings
        self.keep = keep
        self.log = log
        self.name = f"bencher-{uuid.uuid4().hex[:12]}"
        self.share = Share(settings.cache / "runs" / self.name)
        self.errors: list[Error] = []

    @classmethod
    async def perform(
        cls, root: Path, settings: Settings, keep: bool, overrides: list[Override]
    ) -> Report:
        try:
            plan = Plan.load(root.resolve(), overrides)
        except Exception as error:
            message = Reason.of(error)
            print(f"bencher: load: {message}", file=sys.stderr, flush=True)
            values = {o.path: o.value for o in overrides}
            return Report(
                passed=False, overrides=values, errors=[Error(stage="load", message=message)]
            )
        return await cls(plan, settings, keep).start()

    def say(self, text: str) -> None:
        print(f"bencher: {text}", file=self.log, flush=True)

    def fail(self, stage: Stage, error: BaseException) -> None:
        message = Reason.of(error)
        self.say(f"{stage}: {message}")
        self.errors.append(Error(stage=stage, message=message))

    async def create(self, incus: Incus) -> tuple[Guest, Target]:
        plan = self.plan
        self.say(f"launch {self.name}: {plan.runner.type} {plan.runner.image}")
        self.share.path.mkdir(parents=True, exist_ok=True)
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
                    "source": str(self.share.path),
                    "path": "/bencher/run",
                    "readonly": "true",
                },
            },
            timeout=plan.bench.limits.provision.total_seconds(),
        )
        target = Target.of(plan.runner.type, await incus.config(self.name))
        guest = Guest.find(target.family())
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
            raise Halt(f"entrypoint stdout is not a result: {Reason.of(error)}") from None
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
        reporters = {*self.plan.runner.reporters, "system"}
        export = Export.parse(response.text, reporters)
        for stray in sorted(export.strays):
            self.errors.append(
                Error(stage="harvest", message=f"series without a declared reporter: {stray}")
            )
        return {reporter: export.reporter(reporter) for reporter in reporters}

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
            overrides=self.plan.values(),
            rows=lines,
            system=telemetry.get("system", {}),
            expectations=outcomes,
            errors=self.errors,
        )

    def failed(self) -> Report:
        return Report(passed=False, overrides=self.plan.values(), errors=self.errors)

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
            self.share.stage(plan.root, plan.bench, plan.runner, plan.tree, guest.local)
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
        if self.keep:
            self.say(f"kept {self.name}; share at {self.share.path}")
        else:
            try:
                await incus.delete(self.name)
            except Failure as error:
                self.say(f"teardown: {error}")
            self.share.remove()
        await incus.close()
