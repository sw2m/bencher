from __future__ import annotations

import statistics

from bencher.contract.model import Model


class Spread(Model):
    n: int
    min: float
    max: float
    mean: float
    median: float

    @staticmethod
    def over(values: list[float]) -> dict[str, float]:
        return {
            "n": len(values),
            "min": min(values),
            "max": max(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
        }

    @classmethod
    def scalars(cls) -> set[str]:
        return {
            name
            for name, field in cls.model_fields.items()
            if field.annotation in (int, float, float | None)
        }
