from __future__ import annotations

from datetime import timedelta

from pydantic import ByteSize, Field

from bencher.contract.model import Model


class Limits(Model):
    cpu: int = Field(ge=2)  # one CPU is fenced for telemetry
    memory: ByteSize = Field(default=ByteSize(2 * 1024**3))
    provision: timedelta = Field(default=timedelta(minutes=30), gt=timedelta(0))
    run: timedelta = Field(default=timedelta(hours=1), gt=timedelta(0))
