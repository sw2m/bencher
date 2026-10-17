from __future__ import annotations

from pathlib import PurePath
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Plain:
    @staticmethod
    def check(text: str) -> str:
        if not text or text != text.strip() or PurePath(text).name != text or text in (".", ".."):
            raise ValueError(f"{text!r} is not a plain name")
        return text


Name = Annotated[str, AfterValidator(Plain.check)]
