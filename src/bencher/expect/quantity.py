from __future__ import annotations

from typing import Any, Literal

from pydantic import model_validator

from bencher.contract.model import Model
from bencher.contract.output.line import Line
from bencher.measure.stats import Stats
from bencher.measure.summary import Summary

fields = {"samples": Stats.scalars(), "telemetry": Summary.scalars()}


class Quantity(Model):
    kind: Literal["verdict", "samples", "telemetry"]
    key: str = ""
    stat: str = ""

    @model_validator(mode="before")
    @classmethod
    def parse(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if value == "verdict":
            return {"kind": "verdict"}
        kind, *middle, stat = value.split(".")
        key = ".".join(middle)
        if kind not in fields or not key or stat not in fields[kind]:
            raise ValueError(f"bad quantity {value!r}")
        return {"kind": kind, "key": key, "stat": stat}

    def read(self, row: Line) -> float | str | None:
        if self.kind == "verdict":
            return row.verdict
        if self.kind == "samples":
            stats = (row.stats or {}).get(self.key)
            value = getattr(stats, self.stat) if stats else None
        else:
            series = (row.telemetry or {}).get(self.key)
            summary = series.summary if series else None
            value = getattr(summary, self.stat) if summary else None
        return None if value is None else float(value)
