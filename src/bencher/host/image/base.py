from __future__ import annotations

import hashlib
import json
import uuid
from functools import cached_property
from importlib import metadata, resources
from pathlib import Path

import httpx

from bencher.contract.input.runner import Runner
from bencher.host.agent import Agent
from bencher.host.image import Image
from bencher.host.incus import Incus
from bencher.host.log import Log
from bencher.host.store import Store
from bencher.host.target import Target


class Base(Image):
    def __init__(self, runner: Runner, cache: Path, source: str, log: Log) -> None:
        super().__init__(runner, cache, log)
        self.source = source
        self.version = metadata.version("bencher")

    @cached_property
    def key(self) -> str:
        source = self.digest(resources.files("bencher"))
        seed = [self.version, source, self.runner.image, self.runner.type]
        return hashlib.sha256(json.dumps(seed).encode()).hexdigest()[:24]

    def url(self, file: str) -> str:
        return self.source.format(version=self.version, file=file)

    async def fetch(self) -> bool:
        try:
            url = self.url("manifest.json")
            async with httpx.AsyncClient(follow_redirects=True, timeout=600) as client:
                response = await client.get(url)
                if response.status_code == 404:
                    self.log.say(f"no L1 image at {url}, forming locally")
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
        self.log.say(f"image {self.alias}: downloaded {self.url(f'{self.key}.tar')}")
        return True

    async def form(self, incus: Incus, store: Store) -> None:
        self.log.say(f"image {self.alias}: forming from {self.runner.image}")
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
            await self.publish(incus, store, name)
        finally:
            await self.drop(incus, name)
