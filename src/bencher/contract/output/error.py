from __future__ import annotations

from bencher.contract.model import Model
from bencher.contract.output.stage import Stage


class Error(Model):
    stage: Stage
    message: str
