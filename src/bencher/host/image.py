from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import time
import uuid
from functools import cached_property
from importlib import metadata, resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

import httpx

from bencher.contract.input.runner import Runner
from bencher.contract.output.cache import Cache
from bencher.host.agent import Agent
from bencher.host.failure import Failure
from bencher.host.incus import Incus
from bencher.host.log import Log
from bencher.host.store import Store
from bencher.host.target import Target


class Image:
    def __init__(self, runner: Runner, cache: Path, source: str, log: Log) -> None:
        self.runner = runner
        self.images = cache / "images"
        self.source = source
        self.log = log
        self.version = metadata.version("bencher")
        self.deadline = 0.0

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

    @cached_property
    def key(self) -> str:
        source = self.digest(resources.files("bencher"))
        seed = [self.version, source, self.runner.image, self.runner.type]
        return hashlib.sha256(json.dumps(seed).encode()).hexdigest()[:24]

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

    async def ensure(self, incus: Incus, timeout: float) -> Cache:
        self.deadline = time.monotonic() + timeout
        self.images.mkdir(parents=True, exist_ok=True)
        with (self.images / f"{self.key}.lock").open("w") as lock:
            await asyncio.to_thread(fcntl.flock, lock, fcntl.LOCK_EX)
            return await self.resolve(incus, Store(incus))

    async def resolve(self, incus: Incus, store: Store) -> Cache:
        if await store.fingerprint(self.alias) is not None:
            self.log.say(f"image {self.alias}: cached")
            if self.tarball.exists():
                self.tarball.touch()
            return Cache(key=self.key, level="L2")
        if self.tarball.exists() and not await self.verify():
            self.log.say(f"image {self.alias}: discarding {self.tarball}: fails its checksum")
            self.discard()
        level: Literal["L1", "L2"] = "L2"
        if not self.tarball.exists() and await self.fetch():
            self.log.say(f"image {self.alias}: downloaded {self.url(f'{self.key}.tar')}")
            level = "L1"
        if self.tarball.exists():
            self.log.say(f"image {self.alias}: importing {self.tarball}")
            self.tarball.touch()
            try:
                await store.load(self.tarball, self.alias, self.left())
                return Cache(key=self.key, level=level)
            except Failure as error:
                if await self.adopt(store):
                    return Cache(key=self.key, level=level)
                self.log.say(f"image {self.alias}: discarding {self.tarball}: {error}")
                self.discard()
        self.log.say(f"image {self.alias}: forming from {self.runner.image}")
        await self.form(incus, store)
        try:
            await store.export(self.alias, self.partial)
            digest = await asyncio.to_thread(self.hash, self.partial)
            self.partial.rename(self.tarball)
            self.checksum.write_text(digest + "\n")
        except (Failure, OSError) as error:
            self.log.say(f"image {self.alias}: not cached as a tarball: {error}")
            self.partial.unlink(missing_ok=True)
        return Cache(key=self.key, level="L0")

    async def adopt(self, store: Store) -> bool:
        if await store.fingerprint(self.alias) is None:
            return False
        self.log.say(f"image {self.alias}: made concurrently by another run; using it")
        return True

    def url(self, file: str) -> str:
        return self.source.format(version=self.version, file=file)

    async def fetch(self) -> bool:
        url = self.url("manifest.json")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=600) as client:
                response = await client.get(url)
                if response.status_code == 404:
                    return False
                response.raise_for_status()
                manifest = response.json()
                expected = manifest.get(f"{self.key}.tar") if isinstance(manifest, dict) else None
                if expected is None:
                    return False
                sha = hashlib.sha256()
                url = self.url(f"{self.key}.tar")
                async with client.stream("GET", url) as stream:
                    stream.raise_for_status()
                    with self.partial.open("wb") as file:
                        async for chunk in stream.aiter_bytes():
                            sha.update(chunk)
                            file.write(chunk)
        except (httpx.HTTPError, ValueError, OSError) as error:
            self.log.say(f"L1 image unavailable, forming locally: {error}")
            self.partial.unlink(missing_ok=True)
            return False
        if sha.hexdigest() != expected:
            self.log.say(f"L1 image {self.key}.tar fails its digest, forming locally")
            self.partial.unlink(missing_ok=True)
            return False
        self.partial.rename(self.tarball)
        self.checksum.write_text(f"{expected}\n")
        return True

    async def form(self, incus: Incus, store: Store) -> None:
        name = f"bencher-form-{uuid.uuid4().hex[:12]}"
        try:
            await incus.create(name, self.runner.type, self.runner.image, {}, {}, self.left())
            target = Target.of(self.runner.type, await incus.config(name))
            self.log.say(f"cloud-init for {target.distro} {target.release}")
            await incus.configure(name, {"cloud-init.user-data": target.compose()})
            await incus.state(name, "start", min(300, self.left()))
            agent = Agent(incus, name, self.log)
            await agent.wait(self.left())
            await agent.step("clean")
            await incus.state(name, "stop", min(300, self.left()))
            try:
                await store.publish(name, self.alias, self.left())
            except Failure:
                if not await self.adopt(store):
                    raise
        finally:
            try:
                await incus.delete(name)
            except Failure as error:
                self.log.say(f"could not delete {name}: {error}")
