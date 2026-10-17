from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

from bencher.bench import Fixture


def git(*args: str, cwd: Path) -> bytes:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    if done.returncode != 0:
        command = " ".join(["git", *args])
        raise RuntimeError(f"{command}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def resolve(ref: str, clone: Path) -> str:
    return git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", cwd=clone).decode().strip()


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
        git("remote", "add", "origin", fixture.repo, cwd=clone)
    try:
        sha = resolve(fixture.ref, clone) if re.fullmatch(r"[0-9a-f]{40}", fixture.ref) else ""
    except RuntimeError:
        sha = ""
    if not sha:
        git("fetch", "-q", "--tags", "--force", "origin", "+refs/heads/*:refs/heads/*", cwd=clone)
        sha = resolve(fixture.ref, clone)
    tree = clone / "trees" / sha / fixture.runner
    if not tree.is_dir():
        staging = clone / "trees" / f".{sha}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        archive = git("archive", "--format=tar", sha, "--", fixture.runner, cwd=clone)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(staging, filter="data")
        if not (staging / fixture.runner).is_dir():
            raise FileNotFoundError(f"{fixture.runner} is not a directory at {fixture.ref}")
        target = clone / "trees" / sha
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
    return tree
