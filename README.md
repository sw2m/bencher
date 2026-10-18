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
  2. launch     create the Incus instance stopped, with the run share; read the image's os/release
  3. stage      write the run share: bencher's Salt states, the runner, the consumer's inputs, pillar
  4. launch     compose the cloud-config for that os/release, set it as user-data, start
  5. provision  wait for cloud-init: packages, CPU fencing, the runner's states, bencher's states
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

Each instance gets `limits.cpu` and `limits.memory` from the consumer, the composed
`cloud-init.user-data`, and one read-only disk device: the run share at `/bencher/run`.
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
from Incus after the instance is created. Missing intermediate layers are skipped; a missing
release layer fails the run at `launch` and names what is supported.

Layers merge by cloud-init's own default rule
([merging](https://cloudinit.readthedocs.io/en/latest/reference/merging.html)): maps merge
recursively, lists append, scalars are replaced by the more specific layer. Composition happens
on the host, inside a run whenever no cached runner image hits, so the result is one document
that behaves the same under any init system. It is internal: overlays live only in bencher, and
runners provision through Salt.

For Linux the layers install Salt (`salt-common`), Telegraf and VictoriaMetrics from their
signed repositories at pinned versions, write the Salt minion config, declare two slices
(`bencher.slice` for telemetry, `bench.slice` for the benchmark), write the systemd drop-ins that
put both telemetry services in `bencher.slice`, and run:

1. stop the telemetry services, so provisioning isn't recorded;
2. wait for the run share and copy it to local disk (`/var/lib/bencher`), so the measurement
   never touches a share;
3. fence the CPUs: the first CPU of the instance's affinity goes to `bencher.slice`, the rest to
   `bench.slice` (set at runtime with `systemctl set-property`, since a container's CPU ids are
   chosen by Incus per instance);
4. `salt-call --local state.apply runner`, then `state.apply bencher`.

### The guest agent

The layers also install the guest's whole interface to the host: `/etc/bencher/env` (the
entrypoint's environment) and one command, `bencher-agent`, which the host drives through
`incus exec`:

| Command | Does |
| --- | --- |
| `bencher-agent wait` | blocks until provisioning finishes (`cloud-init status --wait`) |
| `bencher-agent log` | prints the provisioning output, shown when provisioning fails |
| `bencher-agent run <seconds>` | runs the entrypoint in `bench.slice` with `/etc/bencher/env`, killed after `<seconds>`; its stdout is the result |
| `bencher-agent flush` | stops the collector, which makes its final gather and flush |

Every OS family's layers provide the same `bencher-agent`, so the host carries no per-family code.

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

### The entrypoint

`/var/lib/bencher/runner/run`, started by `bencher-agent run` from its directory in `bench.slice`,
with the environment in `/etc/bencher/env`:

| Env | Value |
| --- | --- |
| `BENCHER_IN` | `/var/lib/bencher/in`: `params.json`, `artifacts/` |
| `BENCHER_REPORTERS` | where to write `<reporter>.pid` |
| `BENCHER_PUSH_UDP`, `BENCHER_PUSH_HTTP` | the push endpoints above |

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

An OS family is wired when it has cloud layers, and those layers provide `bencher-agent` and
`/etc/bencher/env` (or their family's equivalents). **Linux** (Debian bookworm) is wired.
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

### Operator settings

How this machine runs bencher, layered by pydantic-settings' `CliApp`: a flag beats an
environment variable, which beats the user config file, which beats the default. Global settings
come before the verb (`bencher --socket … run …`); a verb's own settings nest under its name.

| Setting | Flag | Env | Config file | Default |
| --- | --- | --- | --- | --- |
| socket | `--socket` | `BENCHER_SOCKET` | `socket` | the Incus and LXD default sockets |
| cache | `--cache` | `BENCHER_CACHE` | `cache` | `$XDG_CACHE_HOME/bencher` |
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

`mergeable` is the one required status check on `main`. Open PRs as drafts; CI runs when a PR
is marked ready.
