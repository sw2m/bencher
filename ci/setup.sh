#!/bin/sh
set -eu
user=$1
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q incus
incus admin init --minimal
usermod -aG incus-admin "$user"
# Docker on the GitHub runner drops forwarded traffic, which cuts the Incus bridge off
iptables -P FORWARD ACCEPT
if ! command -v nix >/dev/null && [ ! -e /nix/var/nix/profiles/default/bin/nix ]; then
  curl -fsSL https://install.determinate.systems/nix | sh -s -- install --no-confirm
fi
