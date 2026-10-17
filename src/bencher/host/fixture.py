from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

from bencher.contract.bench import Fixture


def git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def fetch(fixture: Fixture, root: Path, cache: Path) -> Path:
    if fixture.path is not None:
        runner = (root / fixture.path / fixture.runner).resolve()
        if not runner.is_dir():
            raise FileNotFoundError(f"fixture runner not found: {runner}")
        return runner
    assert fixture.repo is not None and fixture.ref is not None
    clone = cache / "fixtures" / hashlib.sha256(fixture.repo.encode()).hexdigest()[:16]
    if not (clone / "HEAD").exists():
        clone.mkdir(parents=True, exist_ok=True)
        git("init", "--bare", "-q", ".", cwd=clone)
    git("fetch", "-q", "--depth", "1", fixture.repo, fixture.ref, cwd=clone)
    sha = git("rev-parse", "FETCH_HEAD^{commit}", cwd=clone).strip()
    tree = clone / "trees" / sha / fixture.runner
    if tree.is_dir():
        return tree
    staging = clone / "trees" / f".{sha}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", sha, "--", fixture.runner],
        cwd=clone,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(staging, filter="data")
    if not (staging / fixture.runner).is_dir():
        raise FileNotFoundError(f"{fixture.runner} is not a directory at {fixture.ref}")
    staging.rename(clone / "trees" / sha)
    return tree
