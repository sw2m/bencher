from __future__ import annotations

from pydantic import Field

from bencher.contract.model import Model


class Pin(Model):
    ref: str = Field(min_length=1)
