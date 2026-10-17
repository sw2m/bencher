from __future__ import annotations

from pydantic import Field, StrictFloat, StrictInt

from bencher.contract.model import Model


class Relative(Model):
    times: StrictInt | StrictFloat
    of: dict[str, str] = Field(min_length=1)
