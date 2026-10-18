from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Thread:
    id: int
    online: bool
    node: int
