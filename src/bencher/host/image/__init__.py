from __future__ import annotations

import asyncio
import fcntl
import hashlib
import time
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

from bencher.contract.input.runner import Runner
from bencher.contract.output.cache import Cache
from bencher.host.agent import Agent
from bencher.host.failure import Failure
from bencher.host.incus import Incus
from bencher.host.log import Log
from bencher.host.share import Share
from bencher.host.store import Store


class Image:
    kind: Literal["base", "provisioned"] = "base"

    def __init__(self, runner: Runner, cache: Path, log: Log) -> None:
        self.runner = runner
        self.images = cache / "images"
        self.log = log
        self.deadline = 0.0
        self.resolved: Cache | None = None

    @staticmethod
    def digest(node: Traversable, prefix: str = "") -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for entry in sorted(node.iterdir(), key=lambda e: e.name):
            path = f"{prefix}{entry.name}"
            if entry.name == "__pycache__":
                continue
            if entry.is_dir():
                found += Image.digest(entry, f"{path}/")
            else:
                found.append((path, hashlib.sha256(entry.read_bytes()).hexdigest()))
        return found

    @property
    def key(self) -> str:
        raise NotImplementedError

    @property
    def alias(self) -> str:
        return f"bencher/{self.key}"

    @property
    def tarball(self) -> Path:
        return self.images / f"{self.key}.tar"

    @property
    def partial(self) -> Path:
        return self.tarball.with_suffix(".part")

    @property
    def checksum(self) -> Path:
        return self.tarball.with_suffix(".sha256")

    @staticmethod
    def hash(path: Path) -> str:
        sha = hashlib.sha256()
        with path.open("rb") as file:
            while chunk := file.read(1 << 20):
                sha.update(chunk)
        return sha.hexdigest()

    async def verify(self) -> bool:
        if not self.checksum.exists():
            return False
        expected = self.checksum.read_text().strip()
        return await asyncio.to_thread(self.hash, self.tarball) == expected

    def discard(self) -> None:
        self.tarball.unlink(missing_ok=True)
        self.checksum.unlink(missing_ok=True)

    def left(self) -> float:
        return max(1.0, self.deadline - time.monotonic())

    def cache(self, level: Literal["L0", "L1", "L2"]) -> Cache:
        return Cache(key=self.key, level=level, kind=self.kind)

    async def ensure(self, incus: Incus, timeout: float) -> Cache:
        if self.resolved is not None:
            return self.resolved
        self.deadline = time.monotonic() + timeout
        self.images.mkdir(parents=True, exist_ok=True)
        with (self.images / f"{self.key}.lock").open("w") as lock:
            await asyncio.to_thread(fcntl.flock, lock, fcntl.LOCK_EX)
            self.resolved = await self.resolve(incus, Store(incus))
            return self.resolved

    async def fetch(self) -> bool:
        return False

    async def form(self, incus: Incus, store: Store) -> None:
        raise NotImplementedError

    async def resolve(self, incus: Incus, store: Store) -> Cache:
        if await store.fingerprint(self.alias) is not None:
            self.log.say(f"image {self.alias}: cached")
            if self.tarball.exists():
                self.tarball.touch()
            return self.cache("L2")
        if self.tarball.exists() and not await self.verify():
            self.log.say(f"image {self.alias}: discarding {self.tarball}: fails its checksum")
            self.discard()
        level: Literal["L1", "L2"] = "L2"
        if not self.tarball.exists() and await self.fetch():
            level = "L1"
        if self.tarball.exists():
            self.log.say(f"image {self.alias}: importing {self.tarball}")
            self.tarball.touch()
            try:
                await store.load(self.tarball, self.alias, self.left())
                return self.cache(level)
            except Failure as error:
                if await self.adopt(store):
                    return self.cache(level)
                self.log.say(f"image {self.alias}: discarding {self.tarball}: {error}")
                self.discard()
        await self.form(incus, store)
        try:
            await store.export(self.alias, self.partial)
            digest = await asyncio.to_thread(self.hash, self.partial)
            self.partial.rename(self.tarball)
            self.checksum.write_text(digest + "\n")
        except (Failure, OSError) as error:
            self.log.say(f"image {self.alias}: not cached as a tarball: {error}")
            self.partial.unlink(missing_ok=True)
        return self.cache("L0")

    async def adopt(self, store: Store) -> bool:
        if await store.fingerprint(self.alias) is None:
            return False
        self.log.say(f"image {self.alias}: made concurrently by another run; using it")
        return True

    async def publish(self, incus: Incus, store: Store, name: str) -> None:
        await incus.state(name, "stop", min(300, self.left()))
        try:
            await store.publish(name, self.alias, self.left())
        except Failure:
            if not await self.adopt(store):
                raise

    async def launch(self, incus: Incus, name: str, share: Share) -> Agent:
        await incus.create(
            name, self.runner.type, self.alias, {}, {"run": share.device()}, self.left()
        )
        await incus.state(name, "start", min(300, self.left()))
        agent = Agent(incus, name, self.log)
        await agent.wait(self.left())
        return agent

    async def drop(self, incus: Incus, name: str) -> None:
        try:
            await incus.delete(name)
        except Failure as error:
            self.log.say(f"could not delete {name}: {error}")
