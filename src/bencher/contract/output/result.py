from __future__ import annotations

from pydantic import field_validator

from bencher.contract.model import Model
from bencher.contract.output.row import Row


class Result(Model):
    rows: list[Row]

    @field_validator("rows")
    @classmethod
    def unique(cls, rows: list[Row]) -> list[Row]:
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        for row in rows:
            if row.key() in seen:
                raise ValueError(f"duplicate row key: {row.name} {dict(row.tags)}")
            seen.add(row.key())
        return rows

    @classmethod
    def parse(cls, text: str) -> Result:
        return cls.model_validate_json(text)
