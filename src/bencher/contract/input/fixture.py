from __future__ import annotations

from pydantic import Field, model_validator

from bencher.contract.model import Model


class Fixture(Model):
    repo: str | None = None
    path: str | None = None
    ref: str | None = None
    runner: str = Field(min_length=1)

    @model_validator(mode="after")
    def source(self) -> Fixture:
        if (self.repo is None) == (self.path is None):
            raise ValueError("fixture needs exactly one of repo or path")
        if self.repo is not None and not self.ref:
            raise ValueError("fixture.repo needs a ref")
        return self
