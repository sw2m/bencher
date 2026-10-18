#!/bin/sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
leg=$1
out=$2
corrupt=${3:-}
mkdir -p "$out"
level() { jq -r '.cache.level // "none"' "$1"; }
expect() {
  if [ "$(jq -r .pass "$2")" != true ] || [ "$(level "$2")" != "$1" ]; then
    echo "ci/l1.sh: expected a passing $1 run, got pass=$(jq -r .pass "$2") level=$(level "$2"): $2" >&2
    exit 1
  fi
}

"$root/ci/e2e.sh" "$leg" "$out/seed.json"
key=$(jq -r .cache.key "$out/seed.json")
serve=$(mktemp -d)
cache=$(mktemp -d)
server=
trap 'if [ -n "$server" ]; then kill "$server" 2>/dev/null || true; fi; rm -rf "$serve" "$serve.port" "$cache"' EXIT
incus image export "bencher/$key" "$serve/$key" >/dev/null
mv "$serve/$key".tar* "$serve/$key.tar"
printf '{"%s.tar": "%s"}\n' "$key" "$(sha256sum "$serve/$key.tar" | cut -d' ' -f1)" > "$serve/manifest.json"

python3 - "$serve" > "$serve.port" 2>/dev/null <<'PY' &
import functools
import http.server
import sys


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/hop/"):
            self.send_response(302)
            self.send_header("Location", self.path.removeprefix("/hop"))
            self.end_headers()
            return
        super().do_GET()


server = http.server.ThreadingHTTPServer(
    ("127.0.0.1", 0), functools.partial(Handler, directory=sys.argv[1])
)
print(server.server_address[1], flush=True)
server.serve_forever()
PY
server=$!
tries=0
until [ -s "$serve.port" ] && curl -fsL -o /dev/null "http://127.0.0.1:$(cat "$serve.port")/hop/manifest.json"; do
  tries=$((tries + 1))
  [ "$tries" -lt 50 ] || { echo "ci/l1.sh: the L1 server did not come up" >&2; exit 1; }
  sleep 0.2
done
port=$(cat "$serve.port")
rm -f "$serve.port"
export BENCHER_CACHE="$cache" BENCHER_L1="http://127.0.0.1:$port/hop/{file}"

incus image delete "bencher/$key"
"$root/ci/e2e.sh" "$leg" "$out/l1.json" || true
expect L1 "$out/l1.json"

if [ "$corrupt" = corrupt ]; then
  incus image delete "bencher/$key"
  rm -f "$cache/images/$key.tar" "$cache/images/$key.sha256"
  truncate -s 1000000 "$serve/$key.tar"
  "$root/ci/e2e.sh" "$leg" "$out/corrupt.json" > "$out/corrupt.log" 2>&1 || true
  grep -q "fails its digest" "$out/corrupt.log" || { cat "$out/corrupt.log" >&2; echo "ci/l1.sh: the digest check did not reject the corrupt tarball" >&2; exit 1; }
  expect L0 "$out/corrupt.json"
fi
echo "ci/l1.sh: $leg hit L1 through a redirect${corrupt:+, and the digest check rejected a corrupt tarball}"
