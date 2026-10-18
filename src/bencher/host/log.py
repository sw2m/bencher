from __future__ import annotations

import sys
from typing import TextIO


class Log:
    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream if stream is not None else sys.stderr

    def say(self, text: str) -> None:
        print(f"bencher: {text}", file=self.stream, flush=True)

    def write(self, data: bytes) -> None:
        self.stream.write(data.decode(errors="replace"))
        self.stream.flush()
