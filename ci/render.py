import sys
from pathlib import Path

from bencher.host.target import Target

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for target in Target.every():
    (out / f"{target.distro}.{target.release}.{target.kind}.yaml").write_text(target.compose())
