#!/bin/sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
[ -e /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh ] && . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
leg=$1
out=$2
shift 2
case "$leg" in
  runner) set -- --out "$out" "$@" ;;
  *) set -- --set fixture.runner="$leg" --out "$out" "$@" ;;
esac
nix run "path:$root" -- run "$root/ci/bench" "$@"
