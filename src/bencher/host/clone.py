from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pygit2
from pygit2.enums import CheckoutStrategy

from bencher.contract.input.fixture import Fixture


class Clone:
    def __init__(self, fixture: Fixture, root: Path, cache: Path) -> None:
        self.fixture = fixture
        self.root = root
        self.path = cache / "fixtures" / hashlib.sha256(str(fixture.repo).encode()).hexdigest()[:16]

    def repository(self) -> pygit2.Repository:
        if (self.path / "HEAD").exists():
            return pygit2.Repository(str(self.path))
        return pygit2.init_repository(str(self.path), bare=True)

    def resolve(self, remote: pygit2.Remote) -> pygit2.Oid:
        ref = str(self.fixture.ref)
        heads = {head.name: head.oid for head in remote.list_heads()}
        for name in (f"refs/heads/{ref}", f"refs/tags/{ref}"):
            if name in heads:
                return heads[name]
        try:
            return pygit2.Oid(hex=ref)
        except (ValueError, pygit2.InvalidError):
            raise LookupError(f"{self.fixture.repo} has no branch, tag or commit {ref!r}") from None

    def fetch(self) -> Path:
        fixture = self.fixture
        if fixture.path is not None:
            runner = (self.root / fixture.path / fixture.runner).resolve()
            if not runner.is_dir():
                raise FileNotFoundError(f"fixture runner not found: {runner}")
            return runner
        repo = self.repository()
        remote = repo.remotes.create_anonymous(str(fixture.repo))
        oid = self.resolve(remote)
        local = urlsplit(str(fixture.repo)).scheme == "file"
        depth = 0 if local else 1  # libgit2's local transport can't fetch shallow
        remote.fetch([f"+{oid}:refs/bencher/{oid}"], depth=depth)
        fetched = repo.get(oid)
        if fetched is None:
            raise LookupError(f"{fixture.repo} did not send {oid}")
        commit = fetched.peel(pygit2.Commit)
        tree = self.path / "trees" / str(commit.id) / fixture.runner
        if not tree.is_dir():
            self.checkout(repo, commit, tree)
        return tree

    def checkout(self, repo: pygit2.Repository, commit: pygit2.Commit, tree: Path) -> None:
        try:
            subtree = commit.tree / self.fixture.runner
        except KeyError:
            raise FileNotFoundError(
                f"{self.fixture.runner} is not in {self.fixture.repo} at {self.fixture.ref}"
            ) from None
        if not isinstance(subtree, pygit2.Tree):
            raise FileNotFoundError(
                f"{self.fixture.runner} is not a directory at {self.fixture.ref}"
            )
        staging = tree.with_name(f".{tree.name}")
        shutil.rmtree(staging, ignore_errors=True)
        staging.parent.mkdir(parents=True, exist_ok=True)
        repo.checkout_tree(  # type: ignore[no-untyped-call]
            subtree,
            directory=str(staging),
            strategy=CheckoutStrategy.FORCE | CheckoutStrategy.RECREATE_MISSING,
        )
        staging.rename(tree)
