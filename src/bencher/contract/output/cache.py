from __future__ import annotations

from typing import Literal

from bencher.contract.model import Model


class Cache(Model):
    key: str
    level: Literal["L0", "L1", "L2"] | None
    kind: Literal["base", "provisioned"] = "base"
    base: Cache | None = None
