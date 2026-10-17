import json
import sys
import time
import urllib.parse
import urllib.request

base = "http://127.0.0.1:8428"
query = urllib.parse.urlencode({"match[]": '{reporter="system"}'})
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    urllib.request.urlopen(base + "/internal/force_flush").close()
    with urllib.request.urlopen(f"{base}/api/v1/export?{query}") as response:
        if any(json.loads(line) for line in response if line.strip()):
            sys.exit(0)
    time.sleep(1)
sys.exit("bencher: no system telemetry reached the store within 60s")
