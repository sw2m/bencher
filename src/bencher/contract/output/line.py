from __future__ import annotations

from bencher.contract.output.row import Row
from bencher.measure.series import Series
from bencher.measure.stats import Stats


class Line(Row):
    stats: dict[str, Stats] | None = None
    telemetry: dict[str, Series] | None = None
