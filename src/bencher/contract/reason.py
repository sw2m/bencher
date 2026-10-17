from __future__ import annotations

from pydantic import ValidationError


class Reason:
    @staticmethod
    def of(error: BaseException) -> str:
        if isinstance(error, ValidationError):
            problems = [
                ": ".join(filter(None, [".".join(str(p) for p in e["loc"]), e["msg"]]))
                for e in error.errors()
            ]
            return f"{error.title}: " + "; ".join(problems)
        return str(error) or type(error).__name__
