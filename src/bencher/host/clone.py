from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

from bencher.contract.input.fixture import Fixture


class Clone:
    def __init__(self, fixture: Fixture, root: Path, cache: Path) -> None:
        self.fixture = fixture
        self.root = root
        self.path = cache / "fixtures" / hashlib.sha256(str(fixture.repo).encode()).hexdigest()[:16]

    def git(self, *args: str) -> bytes:
        done = subprocess.run(["git", *args], cwd=self.path, capture_output=True, check=False)
        if done.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)}: {done.stderr.decode(errors='replace').strip()}"
            )
        return done.stdout

    def fetch(self) -> Path:
        fixture = self.fixture
        if fixture.path is not None:
            runner = (self.root / fixture.path / fixture.runner).resolve()
            if not runner.is_dir():
                raise FileNotFoundError(f"fixture runner not found: {runner}")
            return runner
        assert fixture.repo is not None and fixture.ref is not None
        if not (self.path / "HEAD").exists():
            self.path.mkdir(parents=True, exist_ok=True)
            self.git("init", "--bare", "-q", ".")
        self.git("fetch", "-q", "--depth", "1", fixture.repo, fixture.ref)
        sha = self.git("rev-parse", "FETCH_HEAD^{commit}").decode().strip()
        tree = self.path / "trees" / sha / fixture.runner
        if not tree.is_dir():
            self.extract(sha)
        return tree

    def extract(self, sha: str) -> None:
        staging = self.path / "trees" / f".{sha}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        archive = self.git("archive", "--format=tar", sha, "--", self.fixture.runner)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(staging, filter="data")
        if not (staging / self.fixture.runner).is_dir():
            raise FileNotFoundError(
                f"{self.fixture.runner} is not a directory at {self.fixture.ref}"
            )
        staging.rename(self.path / "trees" / sha)
