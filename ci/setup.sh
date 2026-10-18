#!/bin/sh
set -eu
user=$1
command -v apt-get >/dev/null || { echo "ci/setup.sh: needs an apt-based host (Debian or Ubuntu)" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q incus qemu-system-x86 ovmf jq curl
if ! incus profile device get default root pool >/dev/null 2>&1; then
  incus admin init --minimal
fi
usermod -aG incus-admin "$user"
# see https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-on-a-router
if command -v iptables >/dev/null; then
  iptables -P FORWARD ACCEPT
fi
if ! nix --version >/dev/null 2>&1 && ! /nix/var/nix/profiles/default/bin/nix --version >/dev/null 2>&1; then
  curl -fsSL https://install.determinate.systems/nix/tag/v3.23.2 | sh -s -- install --no-confirm
fi
