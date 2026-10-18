from __future__ import annotations

import hashlib
import json
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

release = "https://github.com/sw2m/bencher/releases/download/v{version}/{file}"


class Image:
    def __init__(self, runner: Runner, cache: Path, log: Log) -> None:
        self.runner = runner
        self.images = cache / "images"
        self.log = log
        self.version = metadata.version("bencher")

    @staticmethod
    def digest(node: Traversable, prefix: str = "") -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for entry in sorted(node.iterdir(), key=lambda e: e.name):
            path = f"{prefix}{entry.name}"
            if entry.is_dir():
                found += Image.digest(entry, f"{path}/")
            else:
                found.append((path, hashlib.sha256(entry.read_bytes()).hexdigest()))
        return found

    @cached_property
    def key(self) -> str:
        tree = self.digest(resources.files("bencher").joinpath("cloud"))
        seed = [self.version, tree, self.runner.image, self.runner.type]
        return hashlib.sha256(json.dumps(seed).encode()).hexdigest()[:24]

    @property
    def alias(self) -> str:
        return f"bencher/{self.key}"

    @property
    def tarball(self) -> Path:
        return self.images / f"{self.key}.tar"

    async def ensure(self, incus: Incus, timeout: float) -> Cache:
        store = Store(incus)
        if await store.fingerprint(self.alias) is not None:
            self.log.say(f"image {self.alias}: cached")
            if self.tarball.exists():
                self.tarball.touch()
            return Cache(key=self.key, level="L2")
        self.images.mkdir(parents=True, exist_ok=True)
        level: Literal["L1", "L2"] = "L2"
        if not self.tarball.exists() and await self.fetch():
            self.log.say(f"image {self.alias}: downloaded release v{self.version}")
            level = "L1"
        if self.tarball.exists():
            self.log.say(f"image {self.alias}: importing {self.tarball}")
            self.tarball.touch()
            await store.load(self.tarball, self.alias, timeout)
            return Cache(key=self.key, level=level)
        self.log.say(f"image {self.alias}: forming from {self.runner.image}")
        await self.form(incus, store, timeout)
        await store.export(self.alias, self.partial)
        self.partial.rename(self.tarball)
        return Cache(key=self.key, level="L0")

    @property
    def partial(self) -> Path:
        return self.tarball.with_suffix(".part")

    async def fetch(self) -> bool:
        url = release.format(version=self.version, file="manifest.json")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=600) as client:
                response = await client.get(url)
                if response.status_code == 404:
                    return False
                response.raise_for_status()
                expected = response.json().get(f"{self.key}.tar")
                if expected is None:
                    return False
                sha = hashlib.sha256()
                url = release.format(version=self.version, file=f"{self.key}.tar")
                async with client.stream("GET", url) as stream:
                    stream.raise_for_status()
                    with self.partial.open("wb") as file:
                        async for chunk in stream.aiter_bytes():
                            sha.update(chunk)
                            file.write(chunk)
        except (httpx.HTTPError, ValueError) as error:
            self.log.say(f"release image unavailable, forming locally: {error}")
            self.partial.unlink(missing_ok=True)
            return False
        if sha.hexdigest() != expected:
            self.log.say(f"release image {self.key}.tar fails its digest, forming locally")
            self.partial.unlink()
            return False
        self.partial.rename(self.tarball)
        return True

    async def form(self, incus: Incus, store: Store, timeout: float) -> None:
        name = f"bencher-form-{uuid.uuid4().hex[:12]}"
        try:
            await incus.create(name, self.runner.type, self.runner.image, {}, {}, timeout)
            target = Target.of(self.runner.type, await incus.config(name))
            self.log.say(f"cloud-init for {target.distro} {target.release}")
            await incus.configure(name, {"cloud-init.user-data": target.compose()})
            await incus.state(name, "start", 300)
            agent = Agent(incus, name, self.log)
            await agent.wait(timeout)
            await agent.step("clean")
            await incus.state(name, "stop", 300)
            await store.publish(name, self.alias, timeout)
        finally:
            try:
                await incus.delete(name)
            except Failure as error:
                self.log.say(f"could not delete {name}: {error}")
