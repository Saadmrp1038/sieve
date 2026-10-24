# Secret Generator validation

The reusable integration is in
[`examples/kubernetes-secret-generator`](../../examples/kubernetes-secret-generator/README.md).
VM metadata, SSH configuration, launch scripts, generated oracles, and raw
experiment artifacts are kept outside the committed files.

## Validated environment

- amd64 Linux with Go 1.19.13, kubectl v1.24.10, and Docker 29.1.3.
- kind v0.17.0 with the archive compatibility patch below; Docker Buildx must
  be installed and `docker buildx version` must succeed.
- Python 3.10 with the locked dependencies from the repository root:

```sh
uv sync --locked
```

Run the integration commands with `uv run --locked python`. Set `GOPATH` and
`KUBECONFIG` as described in the main README. No registry push is required.

## kind v0.17.0 and Docker 29

Docker's containerd image store can export `manifest.json` without the legacy
`repositories` archive entry expected by kind v0.17.0. The patch reads tags from
the manifest and removes stale OCI index metadata when retagging the archive.
It includes synthetic archive tests and an optional test against the four
built Kubernetes component archives.

Apply it to a separate kind checkout, using an absolute path to this Sieve
checkout for `SIEVE_SOURCE`:

```sh
SIEVE_SOURCE=/absolute/path/to/sieve
git clone --branch v0.17.0 --depth 1 https://github.com/kubernetes-sigs/kind.git kind-docker29
cd kind-docker29
git apply "$SIEVE_SOURCE/experiments/secret-generator/kind-docker29.patch"
go test ./pkg/build/nodeimage/internal/container/docker
go build -o kind .
```

Put the resulting binary on `PATH`. The patched base commit is
`b5bed7e9f115a19e8d9f300be66c462039937d57`. This is a compatibility patch for
that version, not a claim that newer kind releases require it. Sieve's
`build.py` also adjusts the old Kubernetes builder's Docker bridge address
lookup and reuses a reset source checkout while retaining ignored build output.

## Verified run: 2026-10-24

The final VM run took approximately 26 minutes, reusing the successfully built
instrumented Kubernetes images. It learned twice, rebuilt the test controller,
and produced the following results:

| Check | Result |
| --- | --- |
| Freshly generated plans | 12: 5 restart, 7 missed-state |
| Replayed plans | 12 |
| Completed injections and workloads | 12 / 12 |
| Restart plans with pod deletion and ready replacement | 5 / 5 |
| Missed-state plans with a blocked and released read | 7 / 7 |
| Baseline and test states capturing `kube-root-ca.crt` | 14 / 14 |
| Raw alarms | 0 |

The replay retained 11 schedules byte for byte from the initial experiment.
The Secret deletion schedule (`unobserved-state-test-plan-7`) was amended only
to add the reconciler scope described in the integration README:

- Original SHA-256: `95250e55c68e35ac7d1726da921e33093abff28c114b30ed48a7c196c6798183`.
- Amended SHA-256: `0af9d17348ec2819e48440bcb9a120291d657eea4d4a6adc5b5a827c5b76635f`.

Raw results, both plan versions, baseline snapshots, and action logs were
preserved and reviewed separately from CLI exit codes. Earlier alarms came
from Sieve or the test integration: bootstrap API events were omitted, empty
caches skipped injection, baseline merging counted controller pods twice, and
two schedules could wait for events the test itself prevented. No operator
logic was changed, and this workload found no substantiated operator bug. It
does not establish correctness for other workloads or stale-state faults.

## Regression checks

From the Sieve root:

```sh
uv run --locked pytest
(cd sieve_client && go test -race ./...)
(cd sieve_instrumentation && go test ./...)
```
