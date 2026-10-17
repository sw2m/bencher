from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bencher.measure.series import Series

identity = {"__name__", "reporter", "db", "host", "pid", "parent_pid", "pidfile", "process_name"}


@dataclass
class Export:
    lines: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    strays: set[str] = field(default_factory=set)

    @staticmethod
    def key(metric: dict[str, str]) -> str:
        labels = sorted((k, v) for k, v in metric.items() if k not in identity)
        if not labels:
            return metric["__name__"]
        return metric["__name__"] + "{" + ",".join(f"{k}={v}" for k, v in labels) + "}"

    @classmethod
    def parse(cls, text: str, declared: set[str]) -> Export:
        export = cls()
        for raw in text.splitlines():
            if not raw.strip():
                continue
            line = json.loads(raw)
            reporter = line["metric"].get("reporter")
            if reporter in declared:
                export.lines[reporter].append(line)
            else:
                export.strays.add(cls.key(line["metric"]))
        return export

    def reporter(self, name: str) -> dict[str, Series]:
        sums: dict[str, dict[float, float]] = defaultdict(lambda: defaultdict(float))
        for line in self.lines.get(name, []):
            bucket = sums[self.key(line["metric"])]
            for stamp, value in zip(line["timestamps"], line["values"], strict=True):
                if value is not None:
                    bucket[stamp / 1000] += float(value)
        return {metric: Series.of(points) for metric, points in sorted(sums.items())}
