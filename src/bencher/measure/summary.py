from __future__ import annotations

from bencher.measure.spread import Spread


class Summary(Spread):
    first: float
    last: float
    delta: float

    @classmethod
    def of(cls, values: list[float]) -> Summary | None:
        if not values:
            return None
        return cls(
            **Spread.over(values),
            first=values[0],
            last=values[-1],
            delta=values[-1] - values[0],
        )
