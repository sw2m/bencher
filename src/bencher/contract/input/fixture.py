from __future__ import annotations

from pathlib import Path

from pydantic import Field

from bencher.contract.model import Model


class Fixture(Model):
    path: str = Field(min_length=1)
    runner: str = Field(min_length=1)

    def locate(self, root: Path) -> Path:
        tree = (root / self.path / self.runner).resolve()
        if not tree.is_dir():
            raise FileNotFoundError(f"fixture runner not found: {tree}")
        return tree
