from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

    async def post(
        self, header: dict[str, str] | None, body: Any, timeout: float
    ) -> dict[str, Any]:
        # see sw2m/pyincusd#5
        request = self.incus.client.param_serialize(
            "POST", "/1.0/images", header_params=header, body=body
        )
        try:
            response = await self.incus.client.call_api(*request)
            envelope = json.loads(await response.response.aread())
        except ApiException as error:
            raise Failure.of(error) from None
        if envelope.get("type") == "error" or response.status >= 400:
            raise Failure(f"Incus: {envelope.get('error')}")
        return await self.incus.wait(envelope.get("operation"), timeout)

    async def alias(self, alias: str, fingerprint: str) -> None:
        try:
            await self.api.images_aliases_post(
                image_alias=ImageAliasesPost(name=alias, target=fingerprint)
            )
        except ApiException as error:
            raise Failure.of(error) from None

    async def publish(self, name: str, alias: str, timeout: float) -> None:
        request = ImagesPost(
            source=ImagesPostSource(type="instance", name=name),
            aliases=[ImageAlias(name=alias)],
        )
        await self.post(None, request.to_dict(), timeout)

    async def export(self, alias: str, path: Path) -> None:
        fingerprint = await self.fingerprint(alias)
        if fingerprint is None:
            raise Failure(f"no image {alias}")
        try:
            raw = await self.api.image_export_get_without_preload_content(fingerprint)
            with path.open("wb") as file:
                async for chunk in raw.aiter_bytes():
                    file.write(chunk)
        except ApiException as error:
            raise Failure.of(error) from None

    async def load(self, path: Path, alias: str, timeout: float) -> None:
        header = {"Content-Type": "application/octet-stream"}
        meta = await self.post(header, path.read_bytes(), timeout)
        await self.alias(alias, str(meta["fingerprint"]))
