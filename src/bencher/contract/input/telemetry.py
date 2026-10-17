from __future__ import annotations

from datetime import timedelta

from pydantic import Field

from bencher.contract.model import Model


class Telemetry(Model):
    interval: timedelta = Field(default=timedelta(seconds=1), gt=timedelta(0))
