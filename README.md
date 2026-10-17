# bencher

A benchmark runner shared across sw2m products. bencher boots a sandboxed **runner** in
Incus (or LXD), lets it provision itself with Salt, runs its entrypoint, and returns the
entrypoint's result enriched with telemetry collected inside the runner, checked against the
consumer's committed expectations.

bencher cares only that a run is sandboxed and that its output is compliant with the schemas
below. What happens inside a runner is the runner's business.

## Ownership

Three kinds of repository take part in a benchmark:

| Repo | Owns |
| --- | --- |
| **bencher** (this repo) | the host orchestrator, the runner's bootstrap and telemetry stack, every schema, statistics, expectation evaluation |
| **fixture repo** (e.g. `sw2m/pdbd-bench`) | runners: an image, Salt states, an entrypoint, declared reporters |
| **consumer** (e.g. `sw2m/pdbd`) | every decision: `bench.toml` (which bencher, which fixture runner, artifacts, params, pins, limits) and `expect.tsv` |

Pins follow ownership. Everything bencher owns (Salt, Telegraf, VictoriaMetrics) is pinned by
checksum in [`src/bencher/payload.toml`](src/bencher/payload.toml). Comparators inside a
runner float unless the consumer pins them in `bench.toml`.

## A run

```
bencher run <bench dir>
  1. load      bench.toml, fetch the fixture at its ref, load runner.toml and expect.tsv
  2. payload   download + verify + unpack bencher's pinned components (cached)
  3. stage     write the run share: bencher's Salt states, the runner, the consumer's inputs, pillar
  4. launch    create the Incus instance (container or VM) with two read-only shares and user-data
  5. provision cloud-init runs bencher's bootstrap: copy inputs local, fence CPUs,
               salt-call --local: the runner's states, then bencher's telemetry states
  6. execute   exec the entrypoint, fenced from telemetry; its stdout is the result
  7. harvest   stop the collector, flush the store, export each reporter's series
  8. teardown  delete the instance
  9. report    validate, add stats and telemetry, evaluate expect.tsv, write the report JSON
```

The exit status is `0` when the report passes and `1` otherwise. A report is always written,
including when a stage fails; its `errors` say which stage.

## Sandbox

Incus through [pyincusd](https://github.com/sw2m/pyincusd), over the local unix socket
(`INCUS_SOCKET`, else the Incus and LXD default sockets in turn). A runner declares whether it
is a `container` or a `vm`; everything else about the run is identical between the two.

Each instance gets:

- `limits.cpu` and `limits.memory` from the consumer;
- `cloud-init.user-data`: bencher's bootstrap for the runner's OS;
- two read-only disk devices: the payload at `/bencher/payload` and the run share at
  `/bencher/run`. Containers bind-mount them; VMs get them over virtiofs (9p as fallback),
  mounted by incus-agent.

Nothing is written back to the host over a share. The result comes from the entrypoint's stdout
and telemetry is exported through exec, both over the Incus API.

## Inside the runner

### Bootstrap

bencher's user-data runs once per instance and logs to `/var/log/bencher/provision.log`:

1. wait for both shares;
2. copy the runner and the consumer's inputs to local disk (`/var/lib/bencher`), so the
   measurement never touches a share;
3. fence the CPUs: the first CPU of the instance's cpuset goes to `bencher.slice` (telemetry),
   the rest to `bench.slice` (the entrypoint and everything it starts);
4. `salt-call --local state.apply runner`, then `state.apply bencher`. The runner provisions
   first, so telemetry starts after provisioning and records only the run;
5. write `/run/bencher/provisioned` (`ok`, or `failed`).

### Salt

Salt is bencher's IaC engine, run from the payload as a self-contained onedir bundle. The
runner's states (`salt/runner.sls` in its directory) provision everything the runner needs;
bencher's states start the telemetry stack.

Consumer inputs reach the states as pillar under `bencher`:

| Pillar key | Source |
| --- | --- |
| `bencher:params` | `[params]` in `bench.toml` |
| `bencher:pins` | `[pins]` in `bench.toml` |
| `bencher:artifacts` | artifact name → local path under `/var/lib/bencher/in/artifacts` |
| `bencher:reporters` | `reporters` in `runner.toml` |

### Telemetry

Both run inside the runner, in `bencher.slice`, pinned by bencher:

- **Telegraf** collects: `procstat` per declared reporter (CPU, memory, I/O, threads, file
  descriptors, context switches), plus system-wide `cpu`, `mem`, `diskio`, `net`, `netstat`,
  `nstat`, `kernel` and `system`, tagged `reporter=system`. It also listens for pushed metrics.
- **VictoriaMetrics** stores, on local disk, and isolates every series by its `reporter` label.

**Reporters** are the isolation unit. A runner declares them in `runner.toml`; bencher writes a
`procstat` filter per reporter that matches the PID in `$BENCHER_REPORTERS/<name>.pid` and all
of its children. The entrypoint writes that file when it starts the reporter. Pushed metrics
must carry a declared `reporter` tag.

**Push endpoints**, for metrics a runner reports itself:

| Env | Protocol | Latency |
| --- | --- | --- |
| `BENCHER_PUSH_UDP` | Influx line protocol over UDP, through Telegraf | flushed each interval |
| `BENCHER_PUSH_HTTP` | VictoriaMetrics ingestion (`/write`, `/api/v1/import/prometheus`) | straight to the store |

**Harvest** happens after the entrypoint exits: Telegraf is stopped (a final gather and flush),
VictoriaMetrics is flushed (`/internal/force_flush`), and each reporter's series and the
`system` series are exported. Series of one reporter are summed across its processes.

## Contracts

`bencher schema <bench|runner|result|report>` prints the JSON Schema of each.

### `bench.toml` (consumer)

```toml
[bencher]
ref = "v0.1.0" # which bencher the consumer's CI runs

[fixture]
repo = "https://github.com/sw2m/pdbd-bench" # or: path = "../pdbd-bench"
ref = "3f2a91c"
runner = "transport"

[limits]
cpu = 4
memory = "4GiB"

[telemetry]
interval = "1s"

[params]
duration = "30s"

[pins]
pppd = "2.5.2"

[artifacts]
linkup = "../../result/bin/linkup"
```

Paths are relative to the bench directory. `limits.cpu` is at least 2: one CPU is fenced for
telemetry.

### `runner.toml` (fixture)

```toml
image = "images:debian/12/cloud"
os = "linux"
type = "container" # or vm
reporters = ["linkup", "pppd"]
```

A runner directory holds `runner.toml`, `salt/runner.sls` (and anything it references) and
the entrypoint `run`. Images must carry cloud-init and, for VMs, incus-agent; the `images:`
remote's `cloud` variants do. `image` is `<remote>:<alias>` for the `images` and `ubuntu`
remotes, or a local alias or fingerprint.

### The entrypoint

`/var/lib/bencher/runner/run`, executed from its directory in `bench.slice`, with:

| Env | Value |
| --- | --- |
| `BENCHER_IN` | `/var/lib/bencher/in`: `params.json`, `pins.json`, `artifacts/` |
| `BENCHER_REPORTERS` | where to write `<reporter>.pid` |
| `BENCHER_PUSH_UDP`, `BENCHER_PUSH_HTTP` | the push endpoints above |

- **stdout** is the result and nothing else. Anything that is not one result document fails
  validation.
- **stderr** is the log; bencher echoes it to its own stderr.
- **exit status** non-zero is a runner error; stdout is then ignored.

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
  "system": {"cpu_usage_user{cpu=cpu-total}": {"series": [], "summary": null}},
  "expectations": [
    {"line": 2, "rule": "throughput\t{\"pair\": \"linkup.pppd\"}\tsamples.Mbit/s.median\t>=\t100",
     "checks": [{"tags": {"pair": "linkup.pppd"}, "actual": 141.2, "bound": 100.0, "pass": true}],
     "pass": true, "error": null}
  ],
  "errors": []
}
```

- `stats.ci` is the distribution-free 95% confidence interval of the median, `null` below six
  samples.
- `telemetry` keys are VictoriaMetrics metric names, with any distinguishing labels as
  `name{k=v,...}`. Counters are cumulative, so their `delta` is the run's total.
- `errors` entries are `{stage, message}`, `stage` one of `load`, `payload`, `stage`, `launch`,
  `provision`, `execute`, `harvest`, `report`.

### `expect.tsv` (consumer)

Optional, next to `bench.toml`. Without it a run only reports; with it, a missed rule fails the
run. One rule per line, tab-separated, `#` comments:

```
# name	tags	quantity	op	bound
bulk	{"impl": "core"}	samples.MB/s.median	>=	0.9 * {"impl": "yamux"}
bulk	{"impl": "core"}	telemetry.procstat_memory_rss.max	<=	67108864
parse	{"impl": "socat", "input": "TCP4:h:1"}	verdict	==	err
```

- **tags** is a JSON object matched as a subset of each row's tags; `{}` matches every row of
  that name. Every rule must match at least one row, so stale rules fail.
- **quantity** is `verdict`, `samples.<unit>.<stat>` or `telemetry.<metric>.<stat>`, where
  `<stat>` is a field of the corresponding `stats` or `summary`.
- **op** is one of `<`, `<=`, `==`, `!=`, `>=`, `>`.
- **bound** is a number, `ok`/`err` for verdicts, or `<factor> * <tags>`: the same quantity on
  the row with the same name whose tags are the matched row's tags updated with `<tags>`.

`bencher bless <bench dir> --report <report.json>` snapshots every verdict row into an exact
`verdict == …` rule and keeps every other line verbatim. Measure bounds are only ever set by
hand.

## Operating systems

The guest side is a table keyed by the runner's `os`: its bootstrap, paths, provisioning wait,
fencing and harvest commands. **Linux** is wired. **Windows** (Cloudbase-Init, a virtiofs
automounter service, `run.ps1`) and **BSD** (cloud-init, a 9p or virtiofs client) are paved
by the same table and contracts, and fail at `load` until wired (#3).

## Usage

```sh
nix run github:sw2m/bencher/<ref> -- run crates/mux/bench --out report.json
nix run github:sw2m/bencher/<ref> -- validate crates/mux/bench
nix run github:sw2m/bencher/<ref> -- bless crates/mux/bench --report report.json
nix run github:sw2m/bencher/<ref> -- schema report
```

`--keep` leaves the instance running for inspection. Downloads and fixture checkouts are
cached under `BENCHER_CACHE` (default `$XDG_CACHE_HOME/bencher`).

## Development

```sh
nix flake check   # ruff, mypy --strict, the package build
nix develop       # python, ruff, mypy
```

`mergeable` is the one required status check on `main`. Open PRs as drafts; CI runs when a PR
is marked ready.
