from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import httpx
from pydantic import ValidationError

from bencher.contract.input.override import Override
from bencher.contract.output.cache import Cache
from bencher.contract.output.error import Error
from bencher.contract.output.line import Line
from bencher.contract.output.report import Report
from bencher.contract.output.result import Result
from bencher.contract.output.stage import Stage
from bencher.contract.reason import Reason
from bencher.host.agent import Agent
from bencher.host.cpus import Cpus
from bencher.host.failure import Failure
from bencher.host.image import Image
from bencher.host.image.base import Base
from bencher.host.image.provisioned import Provisioned
from bencher.host.incus import Incus
from bencher.host.log import Log
from bencher.host.settings import Settings
from bencher.host.share import Share
from bencher.lifecycle.halt import Halt
from bencher.lifecycle.plan import Plan
from bencher.measure.export import Export
from bencher.measure.series import Series
from bencher.measure.stats import Stats


class Run:
    def __init__(self, plan: Plan, settings: Settings, keep: bool) -> None:
        self.plan = plan
        self.settings = settings
        self.keep = keep
        self.log = Log()
        self.name = f"bencher-{uuid.uuid4().hex[:12]}"
        self.share = Share(settings.cache / "runs" / self.name)
        self.errors: list[Error] = []
        self.cache: Cache | None = None
        self.base = Base(plan.runner, settings.cache, settings.l1, self.log)
        self.image: Image = (
            Provisioned(plan.runner, settings.cache, self.base, self.share, self.log)
            if plan.bench.cache == "provisioned"
            else self.base
        )

    @classmethod
    async def perform(
        cls, root: Path, settings: Settings, keep: bool, overrides: list[Override]
    ) -> Report:
        try:
            plan = Plan.load(root.resolve(), overrides)
        except Exception as error:
            message = Reason.of(error)
            Log().say(f"load: {message}")
            return Report(
                passed=False,
                overrides=Override.values(overrides),
                errors=[Error(stage="load", message=message)],
            )
        return await cls(plan, settings, keep).start()

    def fail(self, stage: Stage, error: BaseException) -> None:
        message = Reason.of(error)
        self.log.say(f"{stage}: {message}")
        self.errors.append(Error(stage=stage, message=message))

    async def create(self, incus: Incus) -> None:
        plan = self.plan
        limit = plan.bench.limits.provision.total_seconds()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        if self.share.resolver:
            await self.base.ensure(incus, max(1.0, deadline - loop.time()))
            self.share.resolve(await self.base.query(incus, self.share))
        self.cache = await self.image.ensure(incus, max(1.0, deadline - loop.time()))
        count = plan.bench.limits.cpu
        cpus = Cpus(incus)
        await cpus.load()
        if count > cpus.capacity:
            raise Halt(f"limits.cpu is {count}, but this host has {cpus.capacity} online CPUs")
        said = 0.0
        while True:
            async with cpus:
                pinned = await cpus.reserve(count)
                if pinned is not None:
                    self.log.say(
                        f"launch {self.name}: {plan.runner.type} {self.image.alias}, CPUs {pinned}"
                    )
                    await incus.create(
                        self.name,
                        plan.runner.type,
                        self.image.alias,
                        config={
                            "limits.cpu": pinned,
                            "limits.memory": str(int(plan.bench.limits.memory)),
                        },
                        devices={"run": self.share.device()},
                        timeout=max(120.0, deadline - loop.time()),
                    )
                    return
            if loop.time() >= deadline:
                raise Halt(f"{count} CPUs did not come free within {plan.bench.limits.provision}")
            if loop.time() - said >= 60:
                left = int(deadline - loop.time())
                self.log.say(f"waiting for {count} free CPUs, up to {left}s more")
                said = loop.time()
            await asyncio.sleep(5)

    async def provision(self, agent: Agent) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.plan.bench.limits.provision.total_seconds()
        await agent.wait(deadline - loop.time())
        left = deadline - loop.time()
        await agent.step("provision", str(int(left)), timeout=left)

    async def execute(self, agent: Agent) -> Result:
        await agent.step("check")
        done = await agent.run(int(self.plan.bench.limits.run.total_seconds()))
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

    async def harvest(self, incus: Incus, agent: Agent) -> dict[str, dict[str, Series]]:
        await agent.step("flush")
        await agent.step("check")
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
        interval = self.plan.bench.telemetry.interval.total_seconds()
        return {reporter: export.reporter(reporter, interval) for reporter in reporters}

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
            overrides=Override.values(self.plan.overrides),
            cache=self.cache,
            rows=lines,
            system=telemetry.get("system", {}),
            expectations=outcomes,
            errors=self.errors,
        )

    def failed(self) -> Report:
        return Report(
            passed=False,
            overrides=Override.values(self.plan.overrides),
            cache=self.cache,
            errors=self.errors,
        )

    async def start(self) -> Report:
        try:
            incus = Incus(self.settings.socket)
        except Exception as error:
            self.fail("launch", error)
            return self.failed()
        try:
            report = await self.progress(incus)
        finally:
            failure = await self.teardown(incus)
        if failure is None:
            return report
        self.fail("teardown", failure)
        return report.model_copy(update={"passed": False, "errors": self.errors})

    async def progress(self, incus: Incus) -> Report:
        plan = self.plan
        stage: Stage = "launch"
        try:
            stage = "stage"
            self.share.stage(plan.root, plan.bench, plan.runner, plan.tree)
            stage = "launch"
            await self.create(incus)
            await incus.state(self.name, "start", 300)
            agent = Agent(incus, self.name, self.log)
            stage = "provision"
            self.log.say("provision")
            await self.provision(agent)
            stage = "execute"
            self.log.say("execute")
            result = await self.execute(agent)
            stage = "harvest"
            self.log.say("harvest")
            telemetry = await self.harvest(incus, agent)
            stage = "report"
            return self.report(result, telemetry)
        except Exception as error:
            self.fail(stage, error)
            return self.failed()

    async def teardown(self, incus: Incus) -> Failure | None:
        failure = None
        if self.keep:
            self.log.say(f"kept {self.name}; share at {self.share.path}")
        else:
            try:
                await incus.delete(self.name)
            except Failure as error:
                failure = error
            self.share.remove()
        await incus.close()
        return failure
