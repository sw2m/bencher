#!/bin/sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
name=bencher-dev
runner=${1:-runner}
if ! incus info "$name" >/dev/null 2>&1; then
  incus init images:ubuntu/24.04/cloud "$name" --vm \
    -c limits.cpu=4 -c limits.memory=16GiB -c security.secureboot=false \
    -c cloud-init.user-data="$(cat "$root/ci/dev/cloud.yaml")"
  incus config device add "$name" src disk source="$root" path=/src readonly=true
  incus config device override "$name" root size=40GiB
  incus start "$name"
fi
until incus exec "$name" -- cloud-init status --wait >/dev/null 2>&1; do sleep 5; done
incus exec "$name" --user 1000 --group 1000 --env HOME=/home/ubuntu -- \
  sg incus-admin -c "/src/ci/e2e.sh $runner /tmp/report.json"
