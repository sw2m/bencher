from __future__ import annotations

import json
import statistics
from collections import defaultdict
from typing import Any

from bencher.model import Model

identity = {"__name__", "reporter", "db", "host", "pid", "parent_pid", "pidfile", "process_name"}


class Summary(Model):
    n: int
    min: float
    max: float
    mean: float
    median: float
    first: float
    last: float
    delta: float

    @classmethod
    def of(cls, values: list[float]) -> Summary | None:
        if not values:
            return None
        return cls(
            n=len(values),
            min=min(values),
            max=max(values),
            mean=statistics.fmean(values),
            median=statistics.median(values),
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
            if value is None:
                continue
            bucket[stamp / 1000] += float(value)
    out: dict[str, Series] = {}
    for name in sorted(sums):
        points = sorted(sums[name].items())
        out[name] = Series(series=points, summary=Summary.of([v for _, v in points]))
    return out


def parse(text: str) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    strays: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        entry = json.loads(raw)
        if entry["kind"] == "stray":
            strays.append(key(entry["line"]["metric"]))
            continue
        by[entry["reporter"]].append(entry["line"])
    return by, strays
