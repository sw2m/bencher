import json
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

base = "http://127.0.0.1:8428"


def export(match: str) -> Iterator[Any]:
    query = urllib.parse.urlencode({"match[]": match})
    with urllib.request.urlopen(f"{base}/api/v1/export?{query}") as response:
        for raw in response:
            if raw.strip():
                yield json.loads(raw)


urllib.request.urlopen(base + "/internal/force_flush").close()
names = [*sys.argv[1:], "system"]
for name in names:
    for line in export(f'{{reporter="{name}"}}'):
        print(json.dumps({"kind": "series", "reporter": name, "line": line}))
declared = "|".join(n.replace(".", "\\\\.") for n in names)
for line in export(f'{{__name__=~".+",reporter!~"{declared}"}}'):
    print(json.dumps({"kind": "stray", "line": line}))
