from __future__ import annotations

import json

from pyincusd.exceptions import ApiException


class Failure(Exception):
    @classmethod
    def of(cls, error: ApiException) -> Failure:
        try:
            return cls(f"Incus: {json.loads(error.body or '')['error']}")
        except (ValueError, KeyError, TypeError):
            return cls(f"Incus: HTTP {error.status} {error.reason}")
