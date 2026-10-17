from __future__ import annotations

from pathlib import PurePath
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def name(text: str) -> str:
    if not text or text != text.strip() or PurePath(text).name != text or text in (".", ".."):
        raise ValueError(f"{text!r} is not a plain name")
    return text


Name = Annotated[str, AfterValidator(name)]


def explain(error: BaseException) -> str:
    if isinstance(error, ValidationError):
        problems = [
            ": ".join(filter(None, [".".join(str(p) for p in e["loc"]), e["msg"]]))
            for e in error.errors()
        ]
        return f"{error.title}: " + "; ".join(problems)
    return str(error) or type(error).__name__
