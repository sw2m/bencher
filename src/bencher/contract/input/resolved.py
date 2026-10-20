from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Resolved:
    @staticmethod
    def load(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text())
        except OSError as error:
            raise ValueError(f"resolved: {path}: {error.strerror or error}") from None
        except ValueError as error:
            raise ValueError(f"resolved: {path} is not JSON: {error}") from None
        if not isinstance(data, dict):
            raise ValueError(f"resolved: {path} holds JSON that is not an object")
        return data
