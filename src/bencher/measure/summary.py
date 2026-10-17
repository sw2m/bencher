from __future__ import annotations

from typing import Any

from bencher.measure.spread import Spread


class Summary(Spread):
    first: float
    last: float
    delta: float

    @classmethod
    def extra(cls, values: list[float]) -> dict[str, Any]:
        return {"first": values[0], "last": values[-1], "delta": values[-1] - values[0]}
