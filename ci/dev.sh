#!/bin/sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
name=bencher-dev
leg=${1:-runner}
check=${2:-e2e}
case "$leg:$check" in
  runner:e2e|runner:l1|vm:e2e|vm:l1) ;;
  *) echo "usage: ci/dev.sh [runner|vm] [e2e|l1]" >&2; exit 2 ;;
esac
if ! incus info "$name" >/dev/null 2>&1; then
  incus init -q images:ubuntu/24.04/cloud "$name" --vm \
    -c limits.cpu=4 -c limits.memory=16GiB -c security.secureboot=false
  incus config device add "$name" src disk source="$root" path=/src readonly=true
  incus config device override "$name" root size=40GiB
fi
if [ "$(incus config device get "$name" src source)" != "$root" ]; then
  incus config device set "$name" src source="$root"
  [ "$(incus list "$name" -f csv -c s)" != RUNNING ] || incus restart "$name"
fi
[ "$(incus list "$name" -f csv -c s)" = RUNNING ] || incus start "$name"
tries=0
until incus exec "$name" -- true 2>/dev/null; do
  tries=$((tries + 1))
  [ "$tries" -lt 100 ] || { echo "ci/dev.sh: $name agent not up after 300s: $(incus list "$name" -f csv -c s)" >&2; exit 1; }
  sleep 3
done
status=0
incus exec "$name" -- cloud-init status --wait >/dev/null || status=$?
if [ "$status" -ne 0 ]; then
  incus exec "$name" -- cloud-init status --long >&2
  [ "$status" -eq 2 ] || exit 1  # 2 is cloud-init's "done, degraded": the stock image's deprecation warnings
fi
incus exec "$name" -- sh -c 'command -v docker >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io >/dev/null'
incus exec "$name" -- /src/ci/setup.sh ubuntu
status=0
mkdir -p "$root/ci/dev"
case "$check" in
  e2e)
    incus exec "$name" -- sudo -iu ubuntu sg incus-admin -c "/src/ci/e2e.sh $leg /tmp/report.json" || status=$?
    incus file pull "$name/tmp/report.json" "$root/ci/dev/report.$leg.json" || echo "ci/dev.sh: no report to pull" >&2
    ;;
  l1)
    incus exec "$name" -- rm -rf "/tmp/l1.$leg"
    incus exec "$name" -- sudo -iu ubuntu sg incus-admin -c "/src/ci/l1.sh $leg /tmp/l1.$leg corrupt" || status=$?
    rm -rf "$root/ci/dev/l1.$leg"
    incus file pull -r "$name/tmp/l1.$leg" "$root/ci/dev/" || echo "ci/dev.sh: no reports to pull" >&2
    ;;
esac
exit "$status"
