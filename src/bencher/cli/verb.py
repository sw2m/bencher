from __future__ import annotations

from pydantic import BaseModel

from bencher.host.settings import Settings


class Verb(BaseModel):
    def perform(self, settings: Settings) -> int:
        raise NotImplementedError
