from __future__ import annotations

import json
import tomllib
from typing import Any

from bencher.contract import Model


class Override(Model):
    path: str
    value: Any
    tree: dict[str, Any]

    @classmethod
    def parse(cls, text: str) -> Override:
        key, sep, raw = text.partition("=")
        if not sep:
            raise ValueError(f"--set {text!r}: expected <dotted.key>=<value>")
        try:
            tree = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            try:
                tree = tomllib.loads(f"{key} = {json.dumps(raw)}")
            except tomllib.TOMLDecodeError as error:
                raise ValueError(f"--set {text!r}: {error}") from None
        parts: list[str] = []
        node: Any = tree
        while isinstance(node, dict) and len(node) == 1:
            [(part, node)] = node.items()
            parts.append(part)
        return cls(path=".".join(parts), value=node, tree=tree)
