#!/bin/sh
# see README "Bootstrap"
set -u
mkdir -p /var/log/bencher /run/bencher/reporters
exec >>/var/log/bencher/provision.log 2>&1

fail() {
  echo "bencher: $*"
  echo failed >/run/bencher/provisioned
  exit 1
}

python=/bencher/payload/salt/bin/python3
salt=/bencher/payload/salt/salt-call
waited=0
until [ -f /bencher/run/bencher/minion ] && [ -x "$salt" ]; do
  waited=$((waited + 1))
  [ "$waited" -le 600 ] || fail "shares not mounted after 600s"
  sleep 1
done

rm -rf /var/lib/bencher
mkdir -p /var/lib/bencher
cp -a /bencher/run/. /var/lib/bencher/ || fail "copying the run share"
"$python" -I /var/lib/bencher/bencher/fence.py || fail "fencing CPUs"

apply() {
  "$salt" -c /var/lib/bencher/bencher --local --retcode-passthrough --state-output=changes \
    state.apply "$1" || fail "state.apply $1"
}
[ ! -f /var/lib/bencher/runner/salt/runner.sls ] || apply runner
apply bencher
echo ok >/run/bencher/provisioned
