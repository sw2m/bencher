from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar


@dataclass(frozen=True)
class Guest:
    family: str
    local: str
    program: str

    wired: ClassVar[dict[str, Guest]]

    @classmethod
    def find(cls, family: str) -> Guest:
        if family not in cls.wired:
            raise LookupError(
                f"runner family {family!r} is paved but not wired yet (sw2m/bencher#3)"
            )
        return cls.wired[family]

    def env(self) -> dict[str, str]:
        return {
            "BENCHER_IN": f"{self.local}/in",
            "BENCHER_REPORTERS": "/run/bencher/reporters",
            "BENCHER_PUSH_UDP": "127.0.0.1:8094",
            "BENCHER_PUSH_HTTP": "http://127.0.0.1:8428",
        }

    def wait(self) -> list[str]:
        return ["cloud-init", "status", "--wait"]

    def log(self) -> list[str]:
        return ["cat", "/var/log/cloud-init-output.log"]

    def run(self, limit: timedelta) -> list[str]:
        command = [
            "systemd-run",
            "--quiet",
            "--pipe",
            "--wait",
            "--collect",
            "--service-type=exec",
            "--unit=bencher-run",
            "--property=Slice=bench.slice",
            f"--property=RuntimeMaxSec={int(limit.total_seconds())}",
            f"--working-directory={self.local}/runner",
        ]
        command += [f"--setenv={k}={v}" for k, v in self.env().items()]
        return [*command, f"{self.local}/runner/{self.program}"]

    def flush(self) -> list[str]:
        return ["systemctl", "stop", "telegraf.service"]


Guest.wired = {"linux": Guest(family="linux", local="/var/lib/bencher", program="run")}
