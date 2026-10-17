from __future__ import annotations

import math
import statistics

from bencher.model import Model


class Stats(Model):
    n: int
    min: float
    max: float
    mean: float
    median: float
    stdev: float | None
    ci: tuple[float, float] | None

    @classmethod
    def of(cls, values: list[float]) -> Stats:
        ordered = sorted(values)
        return cls(
            n=len(ordered),
            min=ordered[0],
            max=ordered[-1],
            mean=statistics.fmean(ordered),
            median=statistics.median(ordered),
            stdev=statistics.stdev(ordered) if len(ordered) > 1 else None,
            ci=interval(ordered),
        )


def interval(ordered: list[float], level: float = 0.95) -> tuple[float, float] | None:
    n = len(ordered)
    if n < 6:  # the order-statistic interval cannot reach 95% below six samples
        return None
    tail = (1 - level) / 2
    total = 2**n
    mass = 0
    k = 0
    while k < n:
        step = math.comb(n, k)
        if (mass + step) / total > tail:
            break
        mass += step
        k += 1
    if k == 0:
        return None
    return (ordered[k - 1], ordered[n - k])
