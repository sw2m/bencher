from __future__ import annotations

from collections import defaultdict

from bencher.contract.output.line import Line

Key = tuple[str, tuple[tuple[str, str], ...]]


class Table:
    def __init__(self, rows: list[Line]) -> None:
        self.rows = rows
        self.keyed: dict[Key, Line] = {row.key(): row for row in rows}
        self.named: dict[str, list[int]] = defaultdict(list)
        self.postings: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        for i, row in enumerate(rows):
            self.named[row.name].append(i)
            for k, v in row.tags.items():
                self.postings[(row.name, k, v)].append(i)

    def select(self, name: str, tags: dict[str, str]) -> list[Line]:
        lists = [self.postings.get((name, k, v), []) for k, v in tags.items()]
        candidates = min(lists, key=len) if lists else self.named.get(name, [])
        return [
            self.rows[i]
            for i in candidates
            if all(self.rows[i].tags.get(k) == v for k, v in tags.items())
        ]
