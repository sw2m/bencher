from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import IO, Any

import httpx
from pyincusd.api.images_api import ImagesApi
from pyincusd.exceptions import ApiException
from pyincusd.models.image_alias import ImageAlias
from pyincusd.models.image_aliases_post import ImageAliasesPost
from pyincusd.models.images_post import ImagesPost
from pyincusd.models.images_post_source import ImagesPostSource

from bencher.host.failure import Failure
from bencher.host.incus import Incus


class Store:
    def __init__(self, incus: Incus) -> None:
        self.incus = incus
        self.api = ImagesApi(incus.client)

    async def fingerprint(self, alias: str) -> str | None:
        try:
            response = await self.api.image_alias_get(alias)
        except ApiException as error:
            if error.status == 404:
                return None
            raise Failure.of(error) from None
        return response.metadata.target if response.metadata else None

    @staticmethod
    def accept(status: int, body: bytes) -> str | None:
        try:
            envelope = json.loads(body)
        except ValueError:
            raise Failure(f"Incus: HTTP {status} with a body that is not JSON") from None
        if not isinstance(envelope, dict):
            raise Failure(f"Incus: HTTP {status} with an unexpected body")
        if envelope.get("type") == "error" or status >= 400:
            raise Failure(f"Incus: {envelope.get('error') or f'HTTP {status}'}")
        operation = envelope.get("operation")
        return str(operation) if operation else None

    async def alias(self, alias: str, fingerprint: str) -> None:
        try:
            await self.api.images_aliases_post(
                image_alias=ImageAliasesPost(name=alias, target=fingerprint)
            )
        except ApiException as error:
            raise Failure.of(error) from None

    async def publish(self, name: str, alias: str, timeout: float) -> None:
        body = ImagesPost(
            source=ImagesPostSource(type="instance", name=name),
            aliases=[ImageAlias(name=alias)],
        ).to_dict()
        # see sw2m/pyincusd#5
        request = self.incus.client.param_serialize("POST", "/1.0/images", body=body)
        try:
            response = await self.incus.client.call_api(*request, _request_timeout=120)
            data = await response.response.aread()
        except (ApiException, httpx.HTTPError) as error:
            raise Failure(f"Incus: publishing {name}: {error}") from None
        await self.incus.wait(self.accept(response.status, data), timeout)

    async def export(self, alias: str, path: Path) -> None:
        fingerprint = await self.fingerprint(alias)
        if fingerprint is None:
            raise Failure(f"no image {alias}")
        try:
            raw = await self.api.image_export_get_without_preload_content(fingerprint)
            if raw.status_code >= 400:
                raise Failure(f"Incus: exporting {alias}: HTTP {raw.status_code}")
            with path.open("wb") as file:
                async for chunk in raw.aiter_bytes():
                    file.write(chunk)
        except (ApiException, httpx.HTTPError) as error:
            raise Failure(f"Incus: exporting {alias}: {error}") from None

    @staticmethod
    async def chunks(file: IO[bytes]) -> AsyncIterator[bytes]:
        while chunk := file.read(1 << 20):
            yield chunk

    async def load(self, path: Path, alias: str, timeout: float) -> None:
        transport = httpx.AsyncHTTPTransport(uds=self.incus.endpoint)
        header = {"Content-Type": "application/octet-stream"}
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://incus", timeout=timeout
            ) as client:
                with path.open("rb") as file:
                    response = await client.post(
                        "/1.0/images", content=self.chunks(file), headers=header
                    )
        except (httpx.HTTPError, OSError) as error:
            raise Failure(f"Incus: importing {path}: {error}") from None
        meta: dict[str, Any] = await self.incus.wait(
            self.accept(response.status_code, response.content), timeout
        )
        fingerprint = meta.get("fingerprint")
        if not fingerprint:
            raise Failure(f"Incus: importing {path} reported no fingerprint")
        await self.alias(alias, str(fingerprint))
