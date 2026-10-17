from __future__ import annotations

import statistics
from typing import Any, Self

from bencher.contract.model import Model


class Spread(Model):
    n: int
    min: float
    max: float
    mean: float
    median: float

    @classmethod
    def of(cls, values: list[float]) -> Self:
        return cls(
            n=len(values),
            min=min(values),
            max=max(values),
            mean=statistics.fmean(values),
            median=statistics.median(values),
            **cls.extra(values),
        )

    @classmethod
    def extra(cls, values: list[float]) -> dict[str, Any]:
        return {}

    @classmethod
    def scalars(cls) -> set[str]:
        return {
            name
            for name, field in cls.model_fields.items()
            if field.annotation in (int, float, float | None)
        }
