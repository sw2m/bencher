from __future__ import annotations

import asyncio
import fcntl
import tempfile
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from bencher.host.incus import Incus


class Cpus:
    def __init__(self, incus: Incus) -> None:
        self.incus = incus
        self.path = Path(tempfile.gettempdir()) / "bencher.cpus.lock"
        self.file: IO[str] | None = None

    async def __aenter__(self) -> Cpus:
        self.file = self.path.open("a")
        await asyncio.to_thread(fcntl.flock, self.file, fcntl.LOCK_EX)
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None

    @staticmethod
    def parse(value: str) -> set[int]:
        if value.strip().isdigit():
            return set()
        found: set[int] = set()
        for part in value.split(","):
            low, sep, high = part.strip().partition("-")
            if not low:
                continue
            found.update(range(int(low), int(high if sep else low) + 1))
        return found

    @staticmethod
    def render(ids: list[int]) -> str:
        return ",".join(str(i) for i in sorted(ids))

    @staticmethod
    def choose(cores: list[list[int]], taken: set[int], count: int) -> list[int] | None:
        whole = [core for core in cores if not taken & set(core)]
        chosen: list[int] = []
        for core in reversed(whole):
            chosen += core[: count - len(chosen)]
            if len(chosen) == count:
                return chosen
        spare = [i for core in reversed(cores) for i in core if i not in taken and i not in chosen]
        chosen += spare[: count - len(chosen)]
        return chosen if len(chosen) == count else None

    async def cores(self) -> list[list[int]]:
        resources: dict[str, Any] = await self.incus.read("/1.0/resources")
        found = []
        for socket in resources.get("cpu", {}).get("sockets", []):
            for core in socket.get("cores", []):
                ids = sorted(t["id"] for t in core.get("threads", []) if t.get("online"))
                if ids:
                    found.append(ids)
        return sorted(found)

    async def taken(self) -> set[int]:
        instances: list[dict[str, Any]] = await self.incus.read("/1.0/instances", recursion="1")
        found: set[int] = set()
        for instance in instances:
            config = instance.get("expanded_config") or {}
            found |= self.parse(str(config.get("limits.cpu", "")))
        return found

    async def reserve(self, count: int) -> str | None:
        chosen = self.choose(await self.cores(), await self.taken(), count)
        return self.render(chosen) if chosen else None
