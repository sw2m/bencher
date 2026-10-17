from __future__ import annotations

from datetime import timedelta

from bencher.contract.model import Model


class Telemetry(Model):
    interval: timedelta = timedelta(seconds=1)
