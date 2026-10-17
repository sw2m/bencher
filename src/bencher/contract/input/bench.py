from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from deepmerge import Merger

from bencher.contract.input.fixture import Fixture
from bencher.contract.input.limits import Limits
from bencher.contract.input.override import Override
from bencher.contract.input.telemetry import Telemetry
from bencher.contract.model import Model
from bencher.contract.plain import Name

replace = Merger([(dict, ["merge"])], ["override"], ["override"])


class Bench(Model):
    fixture: Fixture
    limits: Limits
    telemetry: Telemetry = Telemetry()
    params: dict[str, Any] = {}
    artifacts: dict[Name, str] = {}

    @classmethod
    def load(cls, root: Path, overrides: list[Override]) -> Bench:
        with (root / "bench.toml").open("rb") as file:
            data = tomllib.load(file)
        for override in overrides:
            replace.merge(data, override.tree)
        return cls.model_validate(data)
