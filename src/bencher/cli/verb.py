from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from bencher.host.settings import Settings


class Verb(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def perform(self, settings: Settings) -> int:
        raise NotImplementedError
