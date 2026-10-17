from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from pyincusd.api.instances_api import InstancesApi
from pyincusd.api.operations_api import OperationsApi
from pyincusd.exceptions import ApiException
from pyincusd.local import Client
from pyincusd.models.certificates_post202_response import CertificatesPost202Response
from pyincusd.models.instance_exec_post import InstanceExecPost
from pyincusd.models.instance_put import InstancePut
from pyincusd.models.instance_source import InstanceSource
from pyincusd.models.instance_state_put import InstanceStatePut
from pyincusd.models.instances_post import InstancesPost

from bencher.host.exec import Exec
from bencher.host.failure import Failure

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
kinds = {"container": "container", "vm": "virtual-machine"}


class Incus:
    def __init__(self, socket: str | None) -> None:
        self.client = Client(socket_path=self.socket(socket))
        self.instances = InstancesApi(self.client)
        self.operations = OperationsApi(self.client)

    @staticmethod
    def socket(chosen: str | None) -> str:
        if chosen:
            return chosen
        for path in sockets:
            if Path(path).exists():
                return path
        raise FileNotFoundError("no Incus or LXD socket found; set BENCHER_SOCKET or --socket")

    @staticmethod
    def source(image: str) -> InstanceSource:
        remote, sep, alias = image.partition(":")
        if not sep:
            return InstanceSource(type="image", alias=image)
        if remote not in remotes:
            raise ValueError(f"unknown image remote {remote!r}; known: {', '.join(remotes)}")
        return InstanceSource(
            type="image", alias=alias, server=remotes[remote], protocol="simplestreams"
        )

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
                raw = await self.operations.operation_wait_get_without_preload_content(
                    ident, timeout=min(left, 60)
                )
                envelope = json.loads(await raw.aread())
            except ApiException as error:
                raise Failure.of(error) from None
            if envelope.get("type") == "error":
                raise Failure(f"Incus: {envelope.get('error')}")
            op = CertificatesPost202Response.model_validate(envelope).metadata
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
            start=False,
            source=self.source(image),
            config=config,
            devices=devices,
            profiles=["default"],
        )
        try:
            response = await self.instances.instances_post(instance=request)
        except ApiException as error:
            raise Failure.of(error) from None
        await self.wait(response.operation, timeout)

    async def config(self, name: str) -> dict[str, str]:
        try:
            response = await self.instances.instance_get(name)
        except ApiException as error:
            raise Failure.of(error) from None
        if response.metadata is None:
            raise Failure(f"instance {name} has no metadata")
        return {k: str(v) for k, v in (response.metadata.config or {}).items()}

    async def configure(self, name: str, config: dict[str, str]) -> None:
        try:
            await self.instances.instance_patch(name, instance=InstancePut(config=config))
        except ApiException as error:
            raise Failure.of(error) from None

    async def address(self, name: str) -> str:
        try:
            response = await self.instances.instance_state_get(name)
        except ApiException as error:
            raise Failure.of(error) from None
        config = await self.config(name)
        nics = {
            v.lower()
            for k, v in config.items()
            if k.startswith("volatile.") and k.endswith(".hwaddr")
        }
        network = (response.metadata.network if response.metadata else None) or {}
        for state in network.values():
            if (state.hwaddr or "").lower() not in nics or state.addresses is None:
                continue
            for entry in state.addresses:
                if entry.family == "inet" and entry.scope == "global" and entry.address:
                    return entry.address
        raise Failure(f"instance {name} has no global IPv4 address on its Incus NIC")

    async def state(self, name: str, action: str, timeout: float) -> None:
        try:
            response = await self.instances.instance_state_put(
                name, state=InstanceStatePut(action=action, force=action == "stop")
            )
        except ApiException as error:
            raise Failure.of(error) from None
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
            raise Failure.of(error) from None
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
                raise Failure.of(error) from None
        return Exec(int(meta.get("return", -1)), streams.get("1", b""), streams.get("2", b""))

    async def delete(self, name: str) -> None:
        with contextlib.suppress(Failure):
            await self.state(name, "stop", 120)
        try:
            response = await self.instances.instance_delete(name)
        except ApiException as error:
            if error.status == 404:
                return
            raise Failure.of(error) from None
        await self.wait(response.operation, 120)
