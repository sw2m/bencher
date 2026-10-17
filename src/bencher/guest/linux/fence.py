import json
import pathlib
import subprocess
import sys


def expand(text: str) -> list[int]:
    cpus: list[int] = []
    for part in text.split(","):
        low, _, high = part.partition("-")
        cpus.extend(range(int(low), int(high or low) + 1))
    return cpus


cpus = expand(pathlib.Path("/sys/fs/cgroup/cpuset.cpus.effective").read_text().strip())
if len(cpus) < 2:
    sys.exit(f"bencher: telemetry fencing needs 2 CPUs, the instance has {len(cpus)}")
groups = {"bencher.slice": cpus[:1], "bench.slice": cpus[1:]}
for unit, group in groups.items():
    value = ",".join(str(c) for c in group)
    subprocess.run(
        ["systemctl", "set-property", "--runtime", unit, f"AllowedCPUs={value}"], check=True
    )
pathlib.Path("/run/bencher/cpus.json").write_text(json.dumps(groups) + "\n")
