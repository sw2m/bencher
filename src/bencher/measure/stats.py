from __future__ import annotations

import math
import statistics

from scipy.stats import quantile_test

from bencher.measure.spread import Spread


class Stats(Spread):
    stdev: float | None
    ci: tuple[float, float] | None

    @classmethod
    def of(cls, values: list[float]) -> Stats:
        interval = quantile_test(values, p=0.5).confidence_interval(0.95)
        bounds = (float(interval.low), float(interval.high))
        return cls(
            **Spread.over(values),
            stdev=statistics.stdev(values) if len(values) > 1 else None,
            ci=None if any(math.isnan(b) for b in bounds) else bounds,
        )
