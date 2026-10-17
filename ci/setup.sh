#!/bin/sh
set -eu
user=$1
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q incus qemu-system-x86 ovmf
incus admin init --minimal
usermod -aG incus-admin "$user"
# see https://linuxcontainers.org/incus/docs/main/howto/network_bridge_firewalld/#prevent-connectivity-issues-with-incus-and-docker
if command -v iptables >/dev/null; then
  iptables -P FORWARD ACCEPT
fi
if ! command -v nix >/dev/null && [ ! -e /nix/var/nix/profiles/default/bin/nix ]; then
  curl -fsSL https://install.determinate.systems/nix | sh -s -- install --no-confirm
fi
