from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyincusd.api.instances_api import InstancesApi
from pyincusd.api.operations_api import OperationsApi
from pyincusd.exceptions import ApiException
from pyincusd.local import Client
from pyincusd.models.instance_exec_post import InstanceExecPost
from pyincusd.models.instance_source import InstanceSource
from pyincusd.models.instance_state_put import InstanceStatePut
from pyincusd.models.instances_post import InstancesPost

sockets = [
    "/var/lib/incus/unix.socket",
    "/run/incus/unix.socket",
    "/var/snap/lxd/common/lxd/unix.socket",
    "/var/lib/lxd/unix.socket",
]
remotes = {
    "images": "https://images.linuxcontainers.org",
    "ubuntu": "https://cloud-images.ubuntu.com/releases",
}


def socket() -> str:
    if override := os.environ.get("INCUS_SOCKET"):
        return override
    for path in sockets:
        if Path(path).exists():
            return path
    raise FileNotFoundError("no Incus or LXD socket found; set INCUS_SOCKET")


def source(image: str) -> InstanceSource:
    remote, sep, alias = image.partition(":")
    if sep and remote in remotes:
        return InstanceSource(
            type="image", alias=alias, server=remotes[remote], protocol="simplestreams"
        )
    if sep:
        raise ValueError(f"unknown image remote {remote!r}; known: {', '.join(remotes)}")
    if re.fullmatch(r"[0-9a-f]{12,64}", image):
        return InstanceSource(type="image", fingerprint=image)
    return InstanceSource(type="image", alias=image)


@dataclass(frozen=True)
class Exec:
    code: int
    stdout: bytes
    stderr: bytes


class Failure(Exception):
    pass


kinds = {"container": "container", "vm": "virtual-machine"}


def reason(error: ApiException) -> str:
    try:
        body = json.loads(error.body or "")
        return f"Incus: {body['error']}"
    except (ValueError, KeyError, TypeError):
        return f"Incus: HTTP {error.status} {error.reason}"


class Incus:
    def __init__(self) -> None:
        self.client = Client(socket_path=socket())
        self.instances = InstancesApi(self.client)
        self.operations = OperationsApi(self.client)

    async def close(self) -> None:
        await self.client.close()  # type: ignore[no-untyped-call]

    async def wait(self, operation: str | None, timeout: float) -> dict[str, Any]:
        if operation is None:
            raise Failure("Incus returned no operation")
        ident = operation.rsplit("/", 1)[-1]
        deadline = time.monotonic() + timeout
        while True:
            left = max(1, int(deadline - time.monotonic()))
            try:
                done = await self.operations.operation_wait_get(ident, timeout=min(left, 60))
            except ApiException as error:
                raise Failure(reason(error)) from None
            op = done.metadata
            if op is None:
                raise Failure(f"operation {ident} has no state")
            if op.status == "Success":
                return op.metadata or {}
            if op.status in ("Failure", "Cancelled"):
                raise Failure(op.err or f"operation {ident} {op.status.lower()}")
            if time.monotonic() >= deadline:
                raise Failure(f"operation {ident} timed out")

    async def create(
        self,
        name: str,
        kind: str,
        image: str,
        config: dict[str, str],
        devices: dict[str, dict[str, str]],
        timeout: float,
    ) -> None:
        request = InstancesPost(
            name=name,
            type=kinds[kind],
            start=True,
            source=source(image),
            config=config,
            devices=devices,
            profiles=["default"],
        )
        try:
            response = await self.instances.instances_post(instance=request)
        except ApiException as error:
            raise Failure(reason(error)) from None
        await self.wait(response.operation, timeout)

    async def exec(self, name: str, command: list[str], timeout: float) -> Exec:
        request = InstanceExecPost(
            command=command,
            interactive=False,
            record_output=True,
            wait_for_websocket=False,
        )
        try:
            response = await self.instances.instance_exec_post(name, var_exec=request)
        except ApiException as error:
            raise Failure(reason(error)) from None
        meta = await self.wait(response.operation, timeout)
        streams: dict[str, bytes] = {}
        for fd, path in (meta.get("output") or {}).items():
            filename = str(path).rsplit("/", 1)[-1]
            try:
                raw = await self.instances.instance_exec_output_get_without_preload_content(
                    name, filename
                )
                streams[str(fd)] = bytes(await raw.aread())
            except ApiException as error:
                raise Failure(reason(error)) from None
        return Exec(int(meta.get("return", -1)), streams.get("1", b""), streams.get("2", b""))

    async def delete(self, name: str) -> None:
        try:
            stop = await self.instances.instance_state_put(
                name, state=InstanceStatePut(action="stop", force=True)
            )
            await self.wait(stop.operation, 120)
        except (ApiException, Failure):
            pass
        try:
            response = await self.instances.instance_delete(name)
        except ApiException as error:
            if error.status == 404:
                return
            raise Failure(reason(error)) from None
        await self.wait(response.operation, 120)
