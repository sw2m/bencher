#!/bin/sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
leg=$1
out=$2
mkdir -p "$out"
"$root/ci/e2e.sh" "$leg" "$out/a.json" > "$out/a.log" 2>&1 &
a=$!
"$root/ci/e2e.sh" "$leg" "$out/b.json" > "$out/b.log" 2>&1 &
b=$!
status=0
wait "$a" || status=1
wait "$b" || status=1
for run in a b; do
  grep '^bencher: launch' "$out/$run.log" || true
  jq -c '{pass, errors, cpu: [.rows[] | select(.reporter == "spin") | .telemetry.procstat_cpu_usage.summary.max]}' "$out/$run.json" || status=1
done
[ "$status" -eq 0 ] || { echo "ci/pair.sh: concurrent $leg runs did not both pass" >&2; exit 1; }
echo "ci/pair.sh: two concurrent $leg runs both passed, fences intact"
