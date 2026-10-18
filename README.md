# bencher

A benchmark runner shared across sw2m products. bencher boots a sandboxed **runner** in
Incus (or LXD), provisions it from a composed cloud-init document and the runner's own Salt
states, runs its entrypoint, and returns the entrypoint's result enriched with telemetry
collected inside the runner, checked against the consumer's committed expectations.

bencher cares only that a run is sandboxed and that its output is compliant with the schemas
below. What happens inside a runner is the runner's business.

## Ownership

Three kinds of repository take part in a benchmark:

| Repo | Owns |
| --- | --- |
| **bencher** (this repo) | the host orchestrator, the cloud-init layers (the runner's base: Salt, Telegraf, VictoriaMetrics), every schema, statistics, expectation evaluation |
| **fixture repo** (e.g. `sw2m/pdbd-bench`) | runners: an image, Salt states, an entrypoint, declared reporters |
| **consumer** (e.g. `sw2m/pdbd`) | every decision: `bench.toml` (which fixture runner, artifacts, params, limits), `expect.tsv`, and which bencher and which fixture checkout it runs |

Pins follow ownership. Everything bencher installs is pinned by package version in its cloud
layers and verified by the vendor's signed repository. The fixture repo is pinned by the
consumer's own checkout of it (a flake input in `flake.lock`, or a CI checkout at a ref);
bencher only reads it from a path. bencher itself is pinned the same way, by the consumer's flake
input or action ref. Comparators inside a runner float unless the consumer pins
them through a param the runner reads, by the runner's own convention (e.g. `[params.pins]`).

## A run

```
bencher run <bench dir>
  1. load       bench.toml (+ --set), locate the fixture runner, load runner.toml and expect.tsv
  2. launch     resolve the runner's formed image (see Images), create the instance from it stopped,
                with the run share
  3. stage      write the run share: bencher's Salt states, the runner, the consumer's inputs, pillar
  4. launch     start
  5. provision  `bencher-agent provision`: copy the share, fence the CPUs, apply the runner's and
                bencher's states
  6. execute    exec the entrypoint, fenced from telemetry; its stdout is the result
  7. harvest    stop the collector, then flush and export the store over HTTP from the host
  8. teardown   delete the instance
  9. report     add stats and telemetry, evaluate expect.tsv, write the report JSON
```

The exit status is `0` when the report passes and `1` otherwise. A report is always written,
including when a stage fails; its `errors` say which stage.

## Sandbox

Incus through [pyincusd](https://github.com/sw2m/pyincusd), over the local unix socket (the
`socket` setting, else the Incus and LXD default sockets in turn). A runner declares whether it
is a `container` or a `vm`; everything else about the run is identical between the two.

Each instance gets `limits.memory` from the consumer, `limits.cpu` as an explicit set of host
CPUs, and one read-only disk device: the run share at `/bencher/run`.

The CPU set is explicit because Incus rebalances instances given a CPU *count* whenever another
instance starts or stops, which would move the CPUs out from under the telemetry fence; an
explicit set is never rebalanced. bencher picks `limits.cpu` CPUs that no instance in any
project has pinned (by CPU id, or by NUMA node through `limits.cpu.nodes`), under a lock at
`/tmp/bencher.cpus.lock` held until the instance exists, so concurrent bencher runs on a host
never pick the same CPUs. It takes **one thread from each of `limits.cpu` untouched physical
cores**, so telemetry and the benchmark never share a core's execution units, and a core one
bencher run has touched is left to it; only when there are too few untouched cores does it take
spare threads of partly used ones. When too few CPUs are free it waits, inside the same
`limits.provision` budget that resolving the image used, then fails at `launch`; a request
larger than the host's online CPUs fails at once. A clustered Incus is refused: the pick is
from the local host's topology.

The guest records the CPU set it fenced, and `bencher-agent check` (run before the entrypoint
and again after the collector stops) fails the run if that set changed. That catches the fence
itself moving. It does **not** catch another instance being scheduled onto our CPUs: pinning
keeps other *bencher* runs off them, but Incus still places count-limited instances anywhere
once free CPUs run out, and host processes are never excluded. On a host shared with
oversubscribed count-limited instances, the benchmark can share its CPUs without the run
noticing (#16). In a VM the guest numbers its vCPUs from 0 regardless of the host ids, and a
pinned VM's CPUs can't change while it runs, so the check there only confirms what Incus
already guarantees.
Containers bind-mount it; VMs get it over virtiofs (9p as fallback), mounted by incus-agent.
Nothing is written back to the host over the share: the result comes from the entrypoint's
stdout over the Incus API, and telemetry from the store over the instance's network address.

## Cloud layers

The runner's base (L1 formation) is one cloud-config composed from layers in
[`src/bencher/cloud/`](src/bencher/cloud), most general first:

```
cloud/common.yaml                          every runner
cloud/<container|vm>.yaml                  one instance type
cloud/<family>/common.yaml                 an OS family (linux)
cloud/<family>/<distro>/common.yaml        a distro (debian)
cloud/<family>/<distro>/<release>.yaml     a release (bookworm) — required: the support matrix
```

The distro and release come from the image's own properties (`image.os`, `image.release`), read
from Incus after the forming instance is created. Missing intermediate layers are skipped; a
missing release layer fails the run at `launch` and names what is supported.

Layers merge by cloud-init's own default rule
([merging](https://cloudinit.readthedocs.io/en/latest/reference/merging.html)): maps merge
recursively, lists append, scalars are replaced by the more specific layer. Composition happens
on the host, inside a run whenever no formed image hits, so the result is one document
that behaves the same under any init system. It is internal: overlays live only in bencher, and
runners provision through Salt.

For Linux the layers install Salt (`salt-common`), Telegraf and VictoriaMetrics from their
signed repositories, and Python 3 from the distro, at pinned versions, write the Salt minion config, declare two slices
(`bencher.slice` for telemetry, `bench.slice` for the benchmark), write the systemd drop-ins that
put both telemetry services in `bencher.slice`, and disable the telemetry services, so the
formed image boots without recording. Nothing in the layers depends on a run: everything per run
is `bencher-agent provision`.

### The guest agent

The layers also install the guest's whole interface to the host: `/etc/bencher/env` (the
entrypoint's environment) and one command, `bencher-agent`, which the host drives through
`incus exec`:

| Command | Does |
| --- | --- |
| `bencher-agent wait` | blocks until cloud-init finishes (`cloud-init status --wait`) |
| `bencher-agent log` | prints the cloud-init output, shown when it fails |
| `bencher-agent clean` | resets cloud-init's state and empties the machine id before the image is published, so each instance gets its own id without systemd's first-boot presets re-enabling the telemetry services |
| `bencher-agent provision <seconds>` | waits up to `<seconds>` (the rest of `limits.provision`) for the run share and copies it to local disk (`/var/lib/bencher`), so the measurement never touches a share; fences the CPUs (the first CPU of the instance's affinity to `bencher.slice`, the rest to `bench.slice`, set at runtime with `systemctl set-property` since a container's CPU ids are chosen by Incus per instance); then `salt-call --local state.apply runner` and `state.apply bencher` |
| `bencher-agent check` | exits 3 if the instance's CPUs are no longer the set `provision` fenced |
| `bencher-agent run <seconds>` | runs the entrypoint in `bench.slice` with `/etc/bencher/env`, killed after `<seconds>`; its stdout is the result |
| `bencher-agent flush` | stops the collector, which makes its final gather and flush |

Every OS family's layers provide the same `bencher-agent`, so the host carries no per-family code.

## Images

A runner's image is formed once and reused. Forming (L0) launches the runner's `image` with the
composed cloud-config, waits for cloud-init, runs `bencher-agent clean`, stops the instance and
publishes it as the Incus image `bencher/<key>`; every run then launches from that image, so a
run never repeats package installation. The key is a digest of the bencher version, bencher's
whole package source (the cloud layers and the code that composes and forms them), and the
runner's `image` and `type`: a change to any of these forms a new image. The key holds the
`image` *name*, not what it currently points to: a floating alias such as
`images:debian/12/cloud` is frozen at whatever it resolved to when the image was formed, until
something in the key changes. That is the point of the cache (a stable base between runs), and
the trade-off (an upstream rebuild doesn't reach a cached image). Delete `bencher/<key>` and its
tarball to re-form.

| Level | Where | Hit when |
| --- | --- | --- |
| L2 | the Incus image `bencher/<key>`, else `<cache>/images/<key>.tar` (imported), checked against `<key>.sha256` beside it | this machine formed or fetched it before |
| L1 | `<key>.tar` from the `l1` source (by default the GitHub release `v<version>`), checked against the source's `manifest.json` | a release formed the same image and type |
| L0 | formed locally, then exported to `<cache>/images/<key>.tar` | nothing else hits |

A release forms L1 images for the in-repo runners (`images:debian/12/cloud`, as a container and
as a VM); a consumer runner on the same image and type downloads them instead of forming (see
Release). L1 is whatever the release attached; a release without the key, or with no `manifest.json` at
all, is a miss. A fetch that fails, or fails its digest, falls back to forming, and says why. The
digest guards against corruption, not tampering: the manifest comes from the same release as the
tarball, so L1 trusts whoever can publish this repository's releases, exactly as running its
code does.

Resolution holds a lock per key (`<cache>/images/<key>.lock`), so concurrent runs sharing a
cache form or fetch an image once and the rest hit L2. A run that loses a race on the Incus alias
to a run with a different cache adopts that run's image. A cached tarball is checked against its
`<key>.sha256` (written when it was exported or downloaded) before import, because Incus imports a
truncated tarball without complaint and only fails when an instance unpacks it; one that fails
the check, has no checksum, or fails to import is discarded, and resolution falls through to L1
and then L0; an image that formed but couldn't be exported still runs,
it just isn't cached as a tarball. A tarball is touched whenever it backs a run, which is what
lets a cache drop the ones a run didn't need.

## Salt

Salt is the runner's IaC engine. The runner's states (`salt/runner.sls` in its directory)
provision everything the runner needs; bencher's states render the Telegraf config, start the
store and the collector, and wait until system telemetry reaches the store. Consumer inputs
reach the states as pillar under `bencher`:

| Pillar key | Source |
| --- | --- |
| `bencher:params` | `[params]` in `bench.toml`, free-form and unvalidated: the runner decides what it reads |
| `bencher:artifacts` | artifact name → its path in the guest, resolved from the `bencher:inputs` grain the layers set |
| `bencher:reporters` | `reporters` in `runner.toml` |
| `bencher:telemetry:interval` | `[telemetry] interval` in `bench.toml` |

## Telemetry

Both run inside the runner, in `bencher.slice`:

- **Telegraf** collects: `procstat` per declared reporter (CPU, memory, I/O, threads, file
  descriptors, context switches), plus system-wide `cpu`, `mem`, `diskio`, `net`, `netstat`,
  `nstat`, `kernel` and `system`, tagged `reporter=system`. It also listens for pushed metrics.
- **VictoriaMetrics** stores, on local disk, and isolates every series by its `reporter` label.
  It listens on the guest's interfaces, so the host reads it directly.

Per-reporter telemetry is exact in both instance types. System-wide counters are what the guest
can see: a VM sees only itself, while a container sees what Incus's lxcfs virtualizes and the
host's view of the rest (disks in particular). Use a VM when system-wide numbers must be the
runner's alone.

**Reporters** are the isolation unit. A runner declares them in `runner.toml`; bencher writes a
`procstat` filter per reporter that matches the PID in `$BENCHER_REPORTERS/<name>.pid` and all
of its children. The entrypoint writes that file when it starts the reporter. Pushed metrics
must carry a declared `reporter` tag; any other series fails the run at `harvest`.

**Push endpoints**, for metrics a runner reports itself:

| Env | Protocol | Latency |
| --- | --- | --- |
| `BENCHER_PUSH_UDP` | Influx line protocol over UDP, through Telegraf | flushed each interval |
| `BENCHER_PUSH_HTTP` | VictoriaMetrics ingestion (`/write`, `/api/v1/import/prometheus`) | straight to the store |

**Harvest** happens after the entrypoint exits: `bencher-agent flush` stops Telegraf (a final
gather and flush), then the host flushes VictoriaMetrics (`/internal/force_flush`) and exports every
series over HTTP. Series of one reporter are summed across its processes, per collection interval
(each process's samples are bucketed to the interval first, so processes sampled a few
milliseconds apart still sum into one point).

## Contracts

`bencher schema <bench|runner|result|report>` prints the JSON Schema of each.

### `bench.toml` (consumer)

```toml
[fixture]
path = "../../../bench" # the consumer's checkout of sw2m/pdbd-bench
runner = "transport"

[limits]
cpu = 4
memory = "4GiB"
provision = "PT30M"
run = 3600

[telemetry]
interval = "PT0.5S"

[params]
duration = "30s"

[params.pins]
pppd = "2.5.2"

[artifacts]
linkup = "../../result/bin/linkup"
```

- Paths are relative to the bench directory.
- `fixture.path` is wherever the consumer puts its checkout of the fixture repo: a git submodule,
  a CI checkout at a ref, or a symlink to a flake input. That checkout is the pin; bencher does
  no fetching.
- `limits.cpu` is at least 2: one CPU is fenced for telemetry. `limits.memory` is a byte size
  (`4GiB`, `4GB`, or bytes).
- Durations are seconds, ISO 8601 (`PT30M`) or `HH:MM:SS`.
- `limits.provision` bounds launching (resolving or forming the runner's image, then waiting for
  free CPUs) and then, separately, the run's own provisioning. `limits.run` bounds the entrypoint.

### `runner.toml` (fixture)

```toml
image = "images:debian/12/cloud"
type = "container" # or vm
reporters = ["linkup", "pppd"]
```

A runner directory holds `runner.toml`, `salt/runner.sls` (and anything it references) and the
entrypoint, `run` on Linux. `image` is `<remote>:<alias>` for the `images` and `ubuntu`
remotes, or a local alias. Images must carry cloud-init and, for VMs, incus-agent; the
`images:` remote's `cloud` variants do.

The runner directory is copied into the run share with its symlinks followed, so runners in one
fixture can share files by linking to each other. A link must resolve inside the fixture checkout
(`fixture.path`); one that leaves it, or dangles, fails the run at `stage`.

### The entrypoint

`/var/lib/bencher/runner/run`, started by `bencher-agent run` from its directory in `bench.slice`,
with the environment in `/etc/bencher/env`:

| Env | Value |
| --- | --- |
| `BENCHER_IN` | `/var/lib/bencher/in`: `params.json`, `artifacts/` |
| `BENCHER_REPORTERS` | where to write `<reporter>.pid` |
| `BENCHER_PUSH_UDP`, `BENCHER_PUSH_HTTP` | the push endpoints above |

- **language**: any executable the guest can run, started through its shebang. Every guest
  has `python3` (3.11 or later) on `PATH`, so an entrypoint written in Python 3 against the
  standard library runs in every OS family bencher wires, which a shell script doesn't (#3).
  Anything else it needs, the runner's Salt states install.
- **stdout** is the result and nothing else. Anything that is not one result document fails
  validation.
- **stderr** is the log; bencher echoes it to its own stderr.
- **exit status** non-zero is a runner error; stdout is then ignored. Incus turns an exec's 126
  and 127 into its own errors, so `bencher-agent` reports an entrypoint's 126/127 as 226/227, and
  a missing or non-executable entrypoint as 2.

### Result (entrypoint stdout)

```json
{
  "rows": [
    {"name": "throughput", "tags": {"pair": "linkup.pppd"}, "reporter": "linkup",
     "samples": {"Mbit/s": [141.2, 139.8, 142.0]}},
    {"name": "parse", "tags": {"impl": "socat", "input": "TCP4:h:1"}, "verdict": "err"}
  ]
}
```

- `name` + `tags` is the row's key and must be unique. Tags exist to be filtered on: by
  expectations, by `bless`, and by consumer CIs.
- A row carries exactly one of `samples` (unit → raw values) or `verdict` (`ok` | `err`).
- `reporter` attaches the reporter's telemetry to the row and must be declared.

### Report (bencher output)

```json
{
  "pass": true,
  "overrides": {},
  "cache": {"key": "f5949fe0ff596a01a84dfcd8", "level": "L2"},
  "rows": [
    {"name": "throughput", "tags": {"pair": "linkup.pppd"}, "reporter": "linkup",
     "samples": {"Mbit/s": [141.2, 139.8, 142.0]},
     "stats": {"Mbit/s": {"n": 3, "min": 139.8, "max": 142.0, "mean": 141.0, "median": 141.2,
                          "stdev": 1.11, "ci": null}},
     "telemetry": {"procstat_cpu_usage": {"series": [[1760600000.0, 98.2]],
                                          "summary": {"n": 1, "min": 98.2, "max": 98.2,
                                                      "mean": 98.2, "median": 98.2,
                                                      "first": 98.2, "last": 98.2,
                                                      "delta": 0.0}}}}
  ],
  "system": {"cpu_usage_user{cpu=\"cpu-total\"}": {"series": [], "summary": null}},
  "expectations": [
    {"line": 2, "rule": "throughput\t{\"pair\": \"linkup.pppd\"}\tsamples.Mbit/s.median\t>=\t100",
     "checks": [{"tags": {"pair": "linkup.pppd"}, "actual": 141.2, "bound": 100.0, "pass": true}],
     "pass": true, "error": null}
  ],
  "errors": []
}
```

- `overrides` is every `--set` applied to `bench.toml` for this run, as dotted key → value. A
  consumer CI can refuse a report whose `overrides` isn't empty.
- `cache` is the runner image the run launched from (see Images): its `key`, and the `level` it
  came from: `L2` (this machine's Incus image or cache tarball), `L1` (downloaded from the
  release) or `L0` (formed by this run). It is `null` when the run failed before an image was
  resolved.
- `stats.ci` is the distribution-free 95% confidence interval of the median
  (`scipy.stats.quantile_test`) as `[low, high]`, `null` when there are too few samples (under
  six) to reach 95%.
- `telemetry` keys are VictoriaMetrics metric names, with any distinguishing labels as
  `name{k="v",...}` (values JSON-quoted). The labels that identify a process or the pipeline
  (`reporter`, `pid`, `parent_pid`, `pidfile`, `process_name`, `host`, `db`) are dropped from the
  key; series differing only in those are summed. Counters are cumulative, so their `delta` is
  the run's total, unless the process restarted mid-run.
- A row's `telemetry` is its own reporter's; `system` is the machine-wide telemetry, which rules
  cannot reference.
- `errors` entries are `{stage, message}`, `stage` one of `load`, `stage`, `launch`,
  `provision`, `execute`, `harvest`, `report`, `teardown`. A failed teardown (a leaked instance)
  fails the report.

### `expect.tsv` (consumer)

Optional, next to `bench.toml`. Without it a run only reports; with it, a missed rule fails the
run. One rule per line, tab-separated, `#` comments; `tags` and `bound` are JSON:

```
# name	tags	quantity	op	bound
bulk	{"impl": "core"}	samples.MB/s.median	>=	{"times": 0.9, "of": {"impl": "yamux"}}
bulk	{"impl": "core"}	telemetry.procstat_memory_rss.max	<=	67108864
parse	{"impl": "socat", "input": "TCP4:h:1"}	verdict	==	"err"
```

- **tags** is matched as a subset of each row's tags; `{}` matches every row of that name.
  Every rule must match at least one row, so stale rules fail.
- **quantity** is `verdict`, `samples.<unit>.<stat>` or `telemetry.<metric>.<stat>`, where
  `<stat>` is a scalar field of the corresponding `stats` or `summary`.
- **op** is one of `<`, `<=`, `==`, `!=`, `>=`, `>`; verdicts take `==` or `!=`. `==` on a
  measured float is legal but rarely what you want.
- **bound** is a number (not a boolean), `"ok"`/`"err"` for verdicts, or
  `{"times": <factor>, "of": <tags>}`: the same quantity on the row with the same name whose tags
  are the matched row's tags updated with `of`. `of` must change the tags. A missing reference row
  or quantity fails the check with `bound: null`, and the outcome's `error` names what was
  missing.

`bencher bless <bench dir> --report <report.json>` rewrites `expect.tsv` from a completed run (it
refuses a report with `errors`), printing every change:

- an exact `verdict == …` rule (tags equal to a row's full tags) is updated to that row's
  verdict;
- an exact rule whose row is gone, and that matches no row at all, is dropped;
- a broader `verdict ==` rule is kept verbatim; rows it already asserts get no exact rule, and rows
  that now contradict it are reported;
- every other verdict row gets a new exact rule, appended;
- every other line (comments, measure rules) is kept verbatim. Measure bounds are only ever set by
  hand.

## Operating systems

An OS family is wired when it has cloud layers, and those layers provide `bencher-agent`,
`/etc/bencher/env` (or their family's equivalents) and `python3` on `PATH`, the one language an
entrypoint can count on in every family. **Linux** (Debian bookworm) is wired.
**Windows** (Cloudbase-Init, a virtiofs automounter service, a PowerShell `bencher-agent`) and
**BSD** (cloud-init, a 9p or virtiofs client) are paved by the same layers and contracts, and
fail at `launch` until wired (#3).

## Usage

```sh
nix run github:sw2m/bencher/<ref> -- run crates/mux/bench --out report.json
nix run github:sw2m/bencher/<ref> -- lint crates/mux/bench
nix run github:sw2m/bencher/<ref> -- bless crates/mux/bench --report report.json
nix run github:sw2m/bencher/<ref> -- schema report
```

### GitHub Action

```yaml
- uses: actions/checkout@v4
- id: bench
  uses: sw2m/bencher@<ref>
  with:
    bench: crates/mux/bench
    args: --set limits.cpu=2
- uses: actions/upload-artifact@v4
  if: always()
  with:
    path: ${{ steps.bench.outputs.report }}
```

The action needs an apt-based runner (`ubuntu-latest`). It installs Incus and nix, restores
`~/.cache/bencher/images` from the Actions cache, and runs `bencher run`. It then keeps only the
image the report says this run used (`cache.key`, its tarball and checksum), and saves the cache
under a fresh key when that changed what was restored. Restores match any earlier save from the
repository (`bencher-images-<os>-`): keys are content-addressed, so benches share images, and the
cache is bounded by GitHub's eviction. Its outputs are `report` (the report JSON's path),
`passed`, and `cache`, the report's cache level (`L0`, `L1` or `L2`); the step fails when the
report does. `args` is split on whitespace and passed as arguments, never evaluated by a shell,
so it can't carry a value with spaces. `cache: "false"` skips the Actions cache, so on a runner
without the Incus image already present the run resolves L1 or L0. bencher itself is pinned by the action's ref.

### Operator settings

How this machine runs bencher, layered by pydantic-settings' `CliApp`: a flag beats an
environment variable, which beats the user config file, which beats the default. Global settings
come before the verb (`bencher --socket … run …`); a verb's own settings nest under its name.

| Setting | Flag | Env | Config file | Default |
| --- | --- | --- | --- | --- |
| socket | `--socket` | `BENCHER_SOCKET` | `socket` | the Incus and LXD default sockets |
| cache | `--cache` | `BENCHER_CACHE` | `cache` | `$XDG_CACHE_HOME/bencher` |
| l1 | `--l1` | `BENCHER_L1` | `l1` | `https://github.com/sw2m/bencher/releases/download/v{version}/{file}` |
| keep | `run --keep` | `BENCHER_RUN__KEEP` | `[run] keep` | `false`: delete the instance after the run |
| out | `run --out` | `BENCHER_RUN__OUT` | `[run] out` | stdout |

The config file is `$XDG_CONFIG_HOME/bencher/config.toml` (or `BENCHER_CONFIG`). Unknown keys
are an error. `bencher --help` and `bencher <verb> --help` list every setting.

### Overrides

`bench.toml` is the consumer's reviewed decision and is not layered. `--set <dotted.key>=<value>`
(repeatable, on `run` and `lint`) overrides one value for one invocation; the value is
parsed as TOML, falling back to a plain string. Every override is recorded in the report's
`overrides`, so a run that didn't use the committed configuration is always visible.

```sh
bencher run crates/mux/bench --set limits.cpu=8 --set params.duration='"10s"'
```

## Development

```sh
nix flake check   # ruff, mypy --strict, the package build, cloud-init schema of every layer
nix develop       # python, ruff, mypy
```

Code is organized as one class per file, with every operation a method on its subject; each
package's `__init__.py` only re-exports.

### End-to-end

`ci/` holds a consumer (`ci/bench/`: `bench.toml`, `expect.tsv`, an artifact) and a fixture runner
in two shapes (`ci/runner/` as a container, `ci/vm/` as a VM, sharing files by symlink). CI's
`run` job does a full run of each on a GitHub runner:

```sh
sudo ci/setup.sh "$USER"                    # Incus, nix, and the runner user in incus-admin
sg incus-admin -c "ci/e2e.sh runner report.json"
sg incus-admin -c "ci/l1.sh runner l1"
sg incus-admin -c "ci/pair.sh runner pair"
```

`ci/l1.sh <leg> <out> [corrupt]` tests L1 without a release. It exports the image `e2e.sh`
formed and serves it, with a `manifest.json`, from a scratch directory through Python's
`http.server` on `127.0.0.1` (one process binds port 0 and reports it, so there is no port
race), behind a 302 redirect as GitHub's release downloads have. With the Incus image removed
and an empty scratch `BENCHER_CACHE`, a run must report `L1`. With `corrupt`, the served tarball
is truncated, and the run must log that it "fails its digest" and fall back to `L0`; CI does that
on the container leg only, since the fallback doesn't depend on the instance type.

`ci/pair.sh` runs two benches on one host, the second started while the first is executing,
and requires both to pass on disjoint CPU sets that never moved.

`ci/dev.sh [runner|vm] [e2e|l1|pair]` does the same locally, inside an Incus VM shaped like the GitHub runner
(Ubuntu 24.04, 4 CPUs, 16 GiB, Docker installed as on the runner, the worktree mounted read-only
at `/src`), and copies the reports to `ci/dev/` on the host. It re-points `/src`
at the invoking worktree and re-runs `setup.sh` on every invocation, so a kept VM follows
whichever worktree runs it and a changed `setup.sh` reaches it; `incus delete -f
bencher-dev` resets it entirely. It runs the worktree as it stands, uncommitted changes included,
which is the point: CI checks out the commit. The `vm` leg needs nested virtualisation on the
host.

On GitHub, nix comes from the pinned `nix-installer-action`; `setup.sh` installs it (a pinned
installer release) only where it is missing.

CI's `action` job runs the same consumer through the repository's own `action.yml` twice. The
first run forms the image (L0); then the job deletes the Incus image, so the second run must
import the tarball the first one cached and report `L2`.

`mergeable` is the one required status check on `main`; it needs `check`, `run` and `action`.
Open PRs as drafts; CI runs when a PR is marked ready, or on demand with `workflow_dispatch`; a
newer push cancels the PR's running CI. Draft and ready runs are cancelled separately, so the
draft-skipping run a push makes can't cancel the ready run that marking the PR ready makes.

### Release

Pushing a tag `v<version>` (matching `pyproject.toml`) runs `release`: each in-repo runner does a
full run with an empty cache, which forms its L1 image; the tarballs and a `manifest.json` of
their sha256 digests are attached to the GitHub release, and each leg's report is kept as an
artifact. The release is published as a **prerelease**. `verify` then downloads the action
exactly as a consumer receives it (GitHub's tarball of the tag), runs it from a cold cache for
each runner, and requires `cache: L1`; that proves the assets, the manifest, and that a
consumer computes the same keys the release formed. Only then does `promote` turn it into a
full release; GitHub treats the newest full release as latest. Publishing waits for every leg; re-running the failed jobs completes a partial
release, since publishing uploads over an existing prerelease rather than failing on it. A
release that has already been promoted is never touched again: its assets are immutable.
