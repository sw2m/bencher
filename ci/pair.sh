#!/bin/sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
leg=$1
out=$2
mkdir -p "$out"
"$root/ci/e2e.sh" "$leg" "$out/a.json" --set params.duration=90 > "$out/a.log" 2>&1 &
a=$!
tries=0
until grep -q '^bencher: execute' "$out/a.log"; do
  kill -0 "$a" 2>/dev/null || { cat "$out/a.log" >&2; echo "ci/pair.sh: the first run ended before it executed" >&2; exit 1; }
  tries=$((tries + 1))
  [ "$tries" -lt 3600 ] || { echo "ci/pair.sh: the first run never reached execute" >&2; exit 1; }
  sleep 0.5
done
"$root/ci/e2e.sh" "$leg" "$out/b.json" > "$out/b.log" 2>&1 &
b=$!
until grep -q '^bencher: launch bencher-' "$out/b.log"; do
  kill -0 "$b" 2>/dev/null || break
  sleep 0.5
done
if grep -q '^bencher: harvest' "$out/a.log"; then
  echo "ci/pair.sh: the first run finished before the second launched, so they never overlapped" >&2
  exit 1
fi
status=0
wait "$a" || status=1
wait "$b" || status=1
cpus() { sed -n 's/^bencher: launch .*, CPUs \([0-9,]*\)$/\1/p' "$out/$1.log" | tr ',' '\n' | sort -n; }
for run in a b; do
  echo "$run: CPUs $(cpus "$run" | paste -sd, -)"
  jq -c '{pass, errors, cpu: [.rows[] | select(.reporter == "spin") | .telemetry.procstat_cpu_usage.summary.max]}' "$out/$run.json" || status=1
done
[ -n "$(cpus a)" ] && [ -n "$(cpus b)" ] || { echo "ci/pair.sh: a run did not report its CPUs" >&2; exit 1; }
cpus b > "$out/b.cpus"
shared=$(cpus a | grep -Fx -f "$out/b.cpus" || true)
[ -z "$shared" ] || { echo "ci/pair.sh: the runs shared CPUs: $shared" >&2; exit 1; }
[ "$status" -eq 0 ] || { echo "ci/pair.sh: concurrent $leg runs did not both pass" >&2; exit 1; }
echo "ci/pair.sh: two concurrent $leg runs both passed on disjoint CPUs"
