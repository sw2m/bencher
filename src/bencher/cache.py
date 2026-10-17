from __future__ import annotations

import os
from pathlib import Path


def root() -> Path:
    if override := os.environ.get("BENCHER_CACHE"):
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "bencher"
