from __future__ import annotations

from bencher.contract.model import Model
from bencher.measure.summary import Summary


class Series(Model):
    series: list[tuple[float, float]]
    summary: Summary | None

    @classmethod
    def of(cls, points: dict[float, float]) -> Series:
        ordered = sorted(points.items())
        return cls(series=ordered, summary=Summary.of([value for stamp, value in ordered]))
