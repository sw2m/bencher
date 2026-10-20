from __future__ import annotations

import hashlib
import json
import uuid
from functools import cached_property
from pathlib import Path
from typing import Literal

from bencher.contract.input.runner import Runner
from bencher.contract.output.cache import Cache
from bencher.host.image import Image
from bencher.host.image.base import Base
from bencher.host.incus import Incus
from bencher.host.log import Log
from bencher.host.share import Share
from bencher.host.store import Store


class Provisioned(Image):
    kind: Literal["base", "provisioned"] = "provisioned"

    def __init__(self, runner: Runner, cache: Path, parent: Base, share: Share, log: Log) -> None:
        super().__init__(runner, cache, log)
        self.parent = parent
        self.share = share

    @cached_property
    def key(self) -> str:
        staged = [
            entry
            for entry in self.digest(self.share.path)
            if not entry[0].startswith("in/artifacts/")
        ]
        seed = [self.parent.key, self.kind, staged]
        return hashlib.sha256(json.dumps(seed).encode()).hexdigest()[:24]

    def cache(self, level: Literal["L0", "L1", "L2"]) -> Cache:
        base = self.parent.found or Cache(key=self.parent.key, level=None)
        return Cache(key=self.key, level=level, kind=self.kind, base=base)

    async def form(self, incus: Incus, store: Store) -> None:
        await self.parent.ensure(incus, self.left())
        self.log.say(f"image {self.alias}: provisioning from {self.parent.alias}")
        name = f"bencher-prime-{uuid.uuid4().hex[:12]}"
        try:
            agent = await self.parent.launch(incus, name, self.share)
            left = self.left()
            await agent.step("prime", str(int(left)), timeout=left)
            await agent.step("clean")
            await self.publish(incus, store, name)
        finally:
            await self.drop(incus, name)
