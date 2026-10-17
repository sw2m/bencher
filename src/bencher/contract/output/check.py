from __future__ import annotations

from pydantic import Field

from bencher.contract.model import Model


class Check(Model):
    tags: dict[str, str]
    actual: float | str | None
    bound: float | str | None
    passed: bool = Field(alias="pass")
