from __future__ import annotations

from datetime import timedelta

from pydantic import ByteSize, Field

from bencher.contract.model import Model


class Limits(Model):
    cpu: int = Field(ge=2)  # one CPU is fenced for telemetry
    memory: ByteSize = Field(default=ByteSize(2 * 1024**3))
    provision: timedelta = timedelta(minutes=30)
    run: timedelta = timedelta(hours=1)
