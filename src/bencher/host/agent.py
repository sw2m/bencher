from __future__ import annotations

import asyncio

from bencher.host.exec import Exec
from bencher.host.failure import Failure
from bencher.host.incus import Incus
from bencher.host.log import Log


class Agent:
    def __init__(self, incus: Incus, name: str, log: Log) -> None:
        self.incus = incus
        self.name = name
        self.log = log

    async def call(self, *args: str, timeout: float = 120) -> Exec:
        return await self.incus.exec(self.name, ["bencher-agent", *args], timeout)

    async def wait(self, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                done = await self.call("wait", timeout=deadline - loop.time())
            except Failure:
                await asyncio.sleep(2)
                continue
            if done.code in (0, 2):  # 2 is cloud-init's "done, degraded"
                return
            if done.code == 1:
                await self.dump()
                raise Failure("cloud-init reported an error; see its output above")
            await asyncio.sleep(2)
        await self.dump()
        raise Failure(f"cloud-init did not finish within {timeout:.0f}s")

    async def dump(self) -> None:
        try:
            done = await self.call("log", timeout=60)
        except Failure as error:
            self.log.say(f"cloud-init output unavailable: {error}")
            return
        self.log.write(done.stdout)

    async def step(self, *args: str, timeout: float = 120) -> None:
        verb = args[0]
        done = await self.call(*args, timeout=timeout)
        self.log.write(done.stdout)
        self.log.write(done.stderr)
        if done.code != 0:
            lines = done.stderr.decode(errors="replace").strip().splitlines()
            reason = f": {lines[-1]}" if lines else ""
            raise Failure(f"bencher-agent {verb} exited {done.code}{reason}")

    async def run(self, seconds: int) -> Exec:
        done = await self.call("run", str(seconds), timeout=seconds + 120)
        self.log.write(done.stderr)
        return done
