from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from bencher.contract import Model
from bencher.measure import Spread

identity = {"__name__", "reporter", "db", "host", "pid", "parent_pid", "pidfile", "process_name"}


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


class Series(Model):
    series: list[tuple[float, float]]
    summary: Summary | None


def key(metric: dict[str, str]) -> str:
    labels = sorted((k, v) for k, v in metric.items() if k not in identity)
    name = metric["__name__"]
    if not labels:
        return name
    return name + "{" + ",".join(f"{k}={v}" for k, v in labels) + "}"


def fold(lines: list[dict[str, Any]]) -> dict[str, Series]:
    sums: dict[str, dict[float, float]] = defaultdict(lambda: defaultdict(float))
    for line in lines:
        bucket = sums[key(line["metric"])]
        for stamp, value in zip(line["timestamps"], line["values"], strict=True):
            if value is not None:
                bucket[stamp / 1000] += float(value)
    out: dict[str, Series] = {}
    for name in sorted(sums):
        points = sorted(sums[name].items())
        out[name] = Series(series=points, summary=Summary.of([value for stamp, value in points]))
    return out


def split(text: str, declared: set[str]) -> tuple[dict[str, dict[str, Series]], list[str]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    strays: set[str] = set()
    for raw in text.splitlines():
        if not raw.strip():
            continue
        line = json.loads(raw)
        reporter = line["metric"].get("reporter")
        if reporter in declared:
            by[reporter].append(line)
        else:
            strays.add(key(line["metric"]))
    return {reporter: fold(lines) for reporter, lines in by.items()}, sorted(strays)
