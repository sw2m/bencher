from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, field_validator, model_validator

from bencher.contract.model import Model

Verdict = Literal["ok", "err"]


class Row(Model):
    name: str = Field(min_length=1)
    tags: dict[str, str] = {}
    reporter: str | None = None
    samples: dict[str, list[float]] | None = None
    verdict: Verdict | None = None

    @model_validator(mode="after")
    def kind(self) -> Row:
        if (self.samples is None) == (self.verdict is None):
            raise ValueError("a row carries exactly one of samples or verdict")
        for unit, values in (self.samples or {}).items():
            if not values:
                raise ValueError(f"samples[{unit!r}] is empty")
            if not all(math.isfinite(v) for v in values):
                raise ValueError(f"samples[{unit!r}] has a non-finite value")
        return self

    def key(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        return (self.name, tuple(sorted(self.tags.items())))


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
