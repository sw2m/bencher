from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any, get_args

import yaml
from deepmerge import Merger

from bencher.contract.input.runner import Runner
from bencher.host.dumper import Dumper

# see https://cloudinit.readthedocs.io/en/latest/reference/merging.html
merger = Merger([(list, ["append"]), (dict, ["merge"])], ["override"], ["override"])
tree = resources.files("bencher").joinpath("cloud")
kinds = get_args(Runner.model_fields["type"].annotation)


@dataclass(frozen=True)
class Target:
    kind: str
    distro: str
    release: str

    @classmethod
    def of(cls, kind: str, config: dict[str, str]) -> Target:
        return cls(
            kind, config.get("image.os", "").lower(), config.get("image.release", "").lower()
        )

    @staticmethod
    def releases() -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for family in tree.iterdir():
            for distro in family.iterdir() if family.is_dir() else []:
                for leaf in distro.iterdir() if distro.is_dir() else []:
                    if leaf.name.endswith(".yaml") and leaf.name != "common.yaml":
                        found.setdefault(distro.name, []).append(leaf.name.removesuffix(".yaml"))
        return found

    @classmethod
    def every(cls) -> list[Target]:
        return [
            cls(kind, distro, release)
            for distro, found in sorted(cls.releases().items())
            for release in sorted(found)
            for kind in kinds
        ]

    def family(self) -> str:
        for entry in tree.iterdir():
            if entry.is_dir() and entry.joinpath(self.distro).is_dir():
                return entry.name
        supported = ", ".join(sorted(self.releases()))
        raise LookupError(
            f"no cloud layers for {self.distro!r}; supported: {supported} "
            "(Windows and BSD: sw2m/bencher#3)"
        )

    def layers(self) -> list[Traversable]:
        family = tree.joinpath(self.family())
        leaf = family.joinpath(self.distro, f"{self.release}.yaml")
        if not leaf.is_file():
            supported = ", ".join(sorted(self.releases()[self.distro]))
            raise LookupError(
                f"no cloud layer for {self.distro} {self.release!r}; supported: {supported}"
            )
        candidates = [
            tree.joinpath("common.yaml"),
            tree.joinpath(f"{self.kind}.yaml"),
            family.joinpath("common.yaml"),
            family.joinpath(self.distro, "common.yaml"),
            leaf,
        ]
        return [layer for layer in candidates if layer.is_file()]

    def compose(self) -> str:
        document: dict[str, Any] = {}
        for layer in self.layers():
            merger.merge(document, yaml.safe_load(layer.read_text()) or {})
        return "#cloud-config\n" + yaml.dump(document, Dumper=Dumper, sort_keys=False)
