from __future__ import annotations

from pydantic import Field

from bencher.contract.model import Model
from bencher.contract.output.check import Check


class Outcome(Model):
    line: int
    rule: str
    checks: list[Check]
    passed: bool = Field(alias="pass")
    error: str | None
