from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

Name = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
Key = Annotated[str, Field(min_length=1, pattern=r"^[^\t\n\r]+$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def seconds(text: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", text)
    if not match:
        raise ValueError(f"not a duration: {text!r}")
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group(2)]
    return float(match.group(1)) * scale


def duration(text: str) -> str:
    seconds(text)
    return text


Duration = Annotated[str, AfterValidator(duration)]


def explain(error: BaseException) -> str:
    if isinstance(error, ValidationError):
        problems = [
            f"{'.'.join(str(p) for p in e['loc']) or error.title}: {e['msg']}"
            for e in error.errors()
        ]
        return f"{error.title}: " + "; ".join(problems)
    return str(error) or type(error).__name__
