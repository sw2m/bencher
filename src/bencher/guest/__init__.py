from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable


@dataclass(frozen=True)
class Guest:
    os: str
    local: str
    python: str

    def files(self) -> Traversable:
        return resources.files("bencher").joinpath("guest", self.os)

    def userdata(self) -> str:
        return self.files().joinpath("bootstrap.sh").read_text()

    def minion(self) -> str:
        return "\n".join(
            [
                "file_client: local",
                f"root_dir: {self.local}/salt",
                "log_file: /var/log/bencher/salt.log",
                "file_roots:",
                "  base:",
                f"    - {self.local}/states",
                f"    - {self.local}/runner/salt",
                "pillar_roots:",
                "  base:",
                f"    - {self.local}/in/pillar",
                "",
            ]
        )

    def env(self) -> dict[str, str]:
        return {
            "BENCHER_IN": f"{self.local}/in",
            "BENCHER_REPORTERS": "/run/bencher/reporters",
            "BENCHER_PUSH_UDP": "127.0.0.1:8094",
            "BENCHER_PUSH_HTTP": "http://127.0.0.1:8428",
        }

    def wait(self) -> list[str]:
        script = "cloud-init status --wait >/dev/null 2>&1; cat /run/bencher/provisioned"
        return ["sh", "-c", script]

    def log(self) -> list[str]:
        return ["cat", "/var/log/bencher/provision.log"]

    def entry(self, limit: float) -> list[str]:
        command = [
            "systemd-run",
            "--quiet",
            "--pipe",
            "--wait",
            "--collect",
            "--service-type=exec",
            "--unit=bencher-run",
            "-p",
            "Slice=bench.slice",
            "-p",
            f"RuntimeMaxSec={int(limit)}",
            f"--working-directory={self.local}/runner",
        ]
        command += [f"--setenv={k}={v}" for k, v in self.env().items()]
        return [*command, f"{self.local}/runner/run"]

    def harvest(self, reporters: list[str]) -> list[str]:
        script = f"{self.local}/bencher/harvest.py"
        return [
            "sh",
            "-c",
            f'systemctl stop bencher-telegraf.service && exec {self.python} -I {script} "$@"',
            "harvest",
            *reporters,
        ]


guests = {
    "linux": Guest(
        os="linux", local="/var/lib/bencher", python="/bencher/payload/salt/bin/python3"
    ),
}


def find(os: str) -> Guest:
    if os not in guests:
        raise LookupError(f"runner os {os!r} is paved but not wired yet (sw2m/bencher#3)")
    return guests[os]
