from __future__ import annotations

import yaml


class Dumper(yaml.SafeDumper):
    def literal(self, text: str) -> yaml.ScalarNode:
        style = "|" if "\n" in text else None
        return self.represent_scalar("tag:yaml.org,2002:str", text, style=style)


Dumper.add_representer(str, Dumper.literal)
