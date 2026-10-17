from __future__ import annotations

from pathlib import PurePath
from typing import Annotated

from pydantic import AfterValidator


class Plain:
    @staticmethod
    def check(text: str) -> str:
        if not text or text != text.strip() or PurePath(text).name != text or text in (".", ".."):
            raise ValueError(f"{text!r} is not a plain name")
        return text


Name = Annotated[str, AfterValidator(Plain.check)]
