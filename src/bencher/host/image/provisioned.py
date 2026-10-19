from __future__ import annotations

import hashlib
import json
import uuid
from functools import cached_property
from pathlib import Path
from typing import Literal

from bencher.contract.input.runner import Runner
from bencher.host.agent import Agent
from bencher.host.image import Image
from bencher.host.image.base import Base
from bencher.host.incus import Incus
from bencher.host.log import Log
from bencher.host.share import Share
from bencher.host.store import Store


class Provisioned(Image):
    kind: Literal["base", "provisioned"] = "provisioned"

    def __init__(self, runner: Runner, cache: Path, base: Base, share: Share, log: Log) -> None:
        super().__init__(runner, cache, log)
        self.under = base
        self.share = share

    @cached_property
    def key(self) -> str:
        staged = [
            entry
            for entry in self.digest(self.share.path)
            if not entry[0].startswith("in/artifacts/")
        ]
        seed = [self.under.key, self.kind, staged]
        return hashlib.sha256(json.dumps(seed).encode()).hexdigest()[:24]

    async def form(self, incus: Incus, store: Store) -> None:
        self.base = await self.under.ensure(incus, self.left())
        self.log.say(f"image {self.alias}: provisioning from {self.under.alias}")
        name = f"bencher-prime-{uuid.uuid4().hex[:12]}"
        try:
            await incus.create(
                name,
                self.runner.type,
                self.under.alias,
                {},
                {"run": self.share.device()},
                self.left(),
            )
            await incus.state(name, "start", min(300, self.left()))
            agent = Agent(incus, name, self.log)
            await agent.wait(self.left())
            left = self.left()
            await agent.step("prime", str(int(left)), timeout=left)
            await agent.step("clean")
            await self.publish(incus, store, name)
        finally:
            await self.drop(incus, name)
