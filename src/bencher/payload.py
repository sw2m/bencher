from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tomllib
import urllib.request
from importlib import resources
from pathlib import Path

from bencher.model import Model


class Component(Model):
    name: str
    os: str
    arch: str
    url: str
    digest: str
    member: str

    def verify(self, path: Path) -> None:
        algorithm, _, expected = self.digest.partition(":")
        hasher = hashlib.new(algorithm)
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1 << 20), b""):
                hasher.update(block)
        if hasher.hexdigest() != expected:
            raise ValueError(f"{self.url}: {algorithm} mismatch")

    def download(self, cache: Path) -> Path:
        target = cache / "downloads" / self.url.rsplit("/", 1)[-1]
        if target.exists():
            self.verify(target)
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(self.url) as response, partial.open("wb") as file:
            shutil.copyfileobj(response, file)
        self.verify(partial)
        partial.rename(target)
        return target

    def unpack(self, archive: Path, into: Path) -> None:
        with tarfile.open(archive) as tar:
            members = [
                m
                for m in tar.getmembers()
                if m.name == self.member or m.name.startswith(self.member + "/")
            ]
            if not members:
                raise ValueError(f"{archive.name} has no {self.member}")
            if len(members) == 1 and members[0].isfile():
                source = tar.extractfile(members[0])
                assert source is not None
                target = into / self.name
                with source, target.open("wb") as file:
                    shutil.copyfileobj(source, file)
                target.chmod(0o755)
                return
            staging = into / f".{self.name}"
            tar.extractall(staging, members=members, filter="data")
            (staging / self.member).rename(into / self.name)
            shutil.rmtree(staging)


def components(os: str, arch: str) -> list[Component]:
    text = resources.files("bencher").joinpath("payload.toml").read_text()
    pinned = [Component.model_validate(c) for c in tomllib.loads(text)["component"]]
    chosen = [c for c in pinned if c.os == os and c.arch == arch]
    if not chosen:
        raise LookupError(f"no pinned payload for {os}/{arch}")
    return chosen


def prepare(os: str, arch: str, cache: Path) -> Path:
    chosen = components(os, arch)
    stamp = hashlib.sha256(
        json.dumps([c.model_dump() for c in chosen], sort_keys=True).encode()
    ).hexdigest()[:16]
    target = cache / "payload" / f"{os}-{arch}-{stamp}"
    if (target / ".ok").exists():
        return target
    staging = target.with_name(f".{target.name}")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for component in chosen:
        component.unpack(component.download(cache), staging)
    (staging / ".ok").write_text(stamp + "\n")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    return target
