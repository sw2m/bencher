from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Exec:
    code: int
    stdout: bytes
    stderr: bytes
