from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import Any

from bencher.host.failure import Failure
from bencher.host.incus import Incus
from bencher.host.thread import Thread


class Cpus:
    path = Path(
        "/tmp/bencher.cpus.lock"
    )  # fixed, not TMPDIR: every bencher on the host must share it

    def __init__(self, incus: Incus) -> None:
        self.incus = incus
        self.fd: int | None = None
        self.cores: list[list[Thread]] = []

    @classmethod
    def open(cls) -> int:
        flags = os.O_RDWR | os.O_NOFOLLOW
        while True:
            # see https://docs.kernel.org/admin-guide/sysctl/fs.html#protected-regular
            try:
                return os.open(cls.path, flags)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise Failure(
                    f"CPU lock {cls.path}: {error.strerror}; it must be a regular file "
                    "that every bencher user can open read-write (mode 0666)"
                ) from None
            with contextlib.suppress(FileExistsError):
                fd = os.open(cls.path, flags | os.O_CREAT | os.O_EXCL, 0o666)
                os.fchmod(fd, 0o666)
                return fd

    async def __aenter__(self) -> Cpus:
        fd = self.open()
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.2)
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    @classmethod
    def parse(cls, value: str) -> set[int]:
        return set() if value.strip().isdigit() else cls.ids(value)

    @staticmethod
    def ids(value: str) -> set[int]:
        found: set[int] = set()
        for part in value.split(","):
            low, sep, high = part.strip().partition("-")
            try:
                found.update(range(int(low), int(high if sep else low) + 1))
            except ValueError:
                continue
        return found

    @staticmethod
    def render(ids: list[int]) -> str:
        return ",".join(str(i) for i in sorted(ids))

    @staticmethod
    def choose(cores: list[list[Thread]], taken: set[int], count: int) -> list[int] | None:
        free = [core for core in cores if not taken & {t.id for t in core}]
        chosen: list[int] = []
        for core in reversed(free):
            online = [t.id for t in core if t.online]
            if online and len(chosen) < count:
                chosen.append(online[0])
        if len(chosen) < count:
            spare = [
                t.id
                for core in reversed(cores)
                for t in core
                if t.online and t.id not in taken and t.id not in chosen
            ]
            chosen += spare[: count - len(chosen)]
        return chosen if len(chosen) == count else None

    @property
    def capacity(self) -> int:
        return sum(1 for core in self.cores for t in core if t.online)

    async def load(self) -> None:
        server: dict[str, Any] = await self.incus.read("/1.0")
        if (server.get("environment") or {}).get("server_clustered"):
            raise Failure("bencher pins CPUs on one host and does not support a clustered Incus")
        resources: dict[str, Any] = await self.incus.read("/1.0/resources")
        cores = []
        for socket in (resources.get("cpu") or {}).get("sockets") or []:
            for core in socket.get("cores") or []:
                threads = [
                    Thread(t["id"], bool(t.get("online")), int(t.get("numa_node", 0)))
                    for t in core.get("threads") or []
                ]
                if threads:
                    cores.append(sorted(threads, key=lambda t: t.id))
        self.cores = sorted(cores, key=lambda core: core[0].id)

    async def taken(self) -> set[int]:
        instances: list[dict[str, Any]] = await self.incus.read(
            "/1.0/instances", recursion="1", **{"all-projects": "true"}
        )
        found: set[int] = set()
        for instance in instances:
            config = instance.get("expanded_config") or {}
            found |= self.parse(str(config.get("limits.cpu", "")))
            nodes = self.ids(str(config.get("limits.cpu.nodes", "")))
            found |= {t.id for core in self.cores for t in core if t.node in nodes}
        return found

    async def reserve(self, count: int) -> str | None:
        chosen = self.choose(self.cores, await self.taken(), count)
        return self.render(chosen) if chosen else None
