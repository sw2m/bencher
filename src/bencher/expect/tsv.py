from __future__ import annotations

import csv
import io
from collections.abc import Iterator


class Tsv(csv.Dialect):
    delimiter = "\t"
    quoting = csv.QUOTE_NONE
    quotechar = None
    escapechar = None
    doublequote = False
    skipinitialspace = False
    lineterminator = ""
    strict = True

    @classmethod
    def row(cls, cells: list[str]) -> str:
        out = io.StringIO()
        csv.writer(out, dialect=cls).writerow(cells)
        return out.getvalue()

    @classmethod
    def rows(cls, text: str) -> Iterator[tuple[int, list[str]]]:
        reader = csv.reader(io.StringIO(text), dialect=cls)
        for cells in reader:
            yield reader.line_num, cells
