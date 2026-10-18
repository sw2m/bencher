from __future__ import annotations

from typing import Any

from pydantic import Field

from bencher.contract.model import Model
from bencher.contract.output.cache import Cache
from bencher.contract.output.error import Error
from bencher.contract.output.line import Line
from bencher.contract.output.outcome import Outcome
from bencher.measure.series import Series


class Report(Model):
    passed: bool = Field(alias="pass")
    overrides: dict[str, Any] = {}
    cache: Cache | None = None
    rows: list[Line] = []
    system: dict[str, Series] = {}
    expectations: list[Outcome] = []
    errors: list[Error] = []

    def dump(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2) + "\n"
