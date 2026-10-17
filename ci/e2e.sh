#!/bin/sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
[ -e /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh ] && . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
nix run "path:$root" -- run "$root/ci/bench" --set fixture.runner="$1" --out "$2"
