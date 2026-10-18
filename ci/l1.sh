#!/bin/sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
leg=$1
out=$2
cache=${BENCHER_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/bencher}
mkdir -p "$out"
level() { jq -r '.cache.level // "none"' "$1"; }
expect() {
  if [ "$(jq -r .pass "$2")" != true ] || [ "$(level "$2")" != "$1" ]; then
    echo "ci/l1.sh: expected a passing $1 run, got pass=$(jq -r .pass "$2") level=$(level "$2"): $2" >&2
    exit 1
  fi
}
forget() {
  incus image delete "bencher/$key" 2>/dev/null || true
  rm -f "$cache/images/$key.tar" "$cache/images/$key.sha256"
}

"$root/ci/e2e.sh" "$leg" "$out/seed.json"
key=$(jq -r .cache.key "$out/seed.json")
serve=$(mktemp -d)
cp "$cache/images/$key.tar" "$serve/"
printf '{"%s.tar": "%s"}\n' "$key" "$(sha256sum "$serve/$key.tar" | cut -d' ' -f1)" > "$serve/manifest.json"
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 -m http.server --bind 127.0.0.1 --directory "$serve" "$port" >/dev/null 2>&1 &
server=$!
trap 'kill "$server" 2>/dev/null; rm -rf "$serve"' EXIT
tries=0
until curl -fs -o /dev/null "http://127.0.0.1:$port/manifest.json"; do
  tries=$((tries + 1))
  [ "$tries" -lt 50 ] || { echo "ci/l1.sh: http.server did not come up on $port" >&2; exit 1; }
  sleep 0.2
done
export BENCHER_L1="http://127.0.0.1:$port/{file}"

forget
"$root/ci/e2e.sh" "$leg" "$out/l1.json" || true
expect L1 "$out/l1.json"

forget
truncate -s 1000000 "$serve/$key.tar"
"$root/ci/e2e.sh" "$leg" "$out/corrupt.json" || true
expect L0 "$out/corrupt.json"
echo "ci/l1.sh: $leg hit L1 from the served tarball and fell back to L0 when it was corrupt"
