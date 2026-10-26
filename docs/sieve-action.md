# Running Sieve in GitHub Actions

The fork's [reusable workflow](../.github/workflows/run-sieve.yml) accepts an
existing controller integration and runs learning and generated fault plans on
Ubuntu 22.04. For the maintained Secret Generator acceptance suite and its
independent evidence checks, see [acceptance validation](acceptance.md).

## Source selection

The harness and integration are separate checkouts. Both revisions are required
full, lowercase, 40-character Git commit SHAs. Branches, tags and abbreviated
hashes are rejected. The workflow checks actual `HEAD` values and clean source
before executing the integration, then uploads both source records with results.
The Sieve checkout always comes from `Saadmrp1038/sieve`. Integration assets are
installed from the verified Git archive, excluding ignored host files. The profile
must be standalone; symbolic links must remain within it.

GitHub's reusable-workflow context belongs to the caller. Pass the Sieve commit
explicitly; an external integration repository's event SHA identifies different
code. See [GitHub's reuse documentation](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
and [checkout's commit examples](https://github.com/actions/checkout#checkout-pull-request-head-commit-instead-of-merge-commit).

| Input | Meaning |
|---|---|
| `sieve-revision` | Exact Sieve fork commit to test |
| `controller-name` | Controller identifier under `controllers/` |
| `github-repo` | Integration repository in `owner/repository` form |
| `github-ref` | Exact integration commit |
| `subdirectory` | Integration directory containing `config.json`, build/deploy assets and workloads |
| `workload_name` | Workload to execute |

For a caller in this fork, use `${{ github.event.pull_request.head.sha || github.sha }}`
for `sieve-revision`. For external callers, configure the desired Sieve SHA
explicitly. Pin the reusable workflow's `uses` reference to a reviewed commit too.
The workflow does not require Docker Hub credentials for public images.

```yaml
jobs:
  experiment:
    uses: Saadmrp1038/sieve/.github/workflows/run-sieve.yml@main
    with:
      sieve-revision: ${{ vars.SIEVE_REVISION }}
      controller-name: example-operator
      github-repo: your-account/operator-integration
      github-ref: ${{ vars.INTEGRATION_REVISION }}
      subdirectory: example-operator
      workload_name: recreate
```

Both variables in this example must contain full commit SHAs. Complete the
integration using the [porting guide](port.md) and supply compatible instrumented
Kubernetes node images for its profile. The workflow builds learning/testing
controller images, learns two baselines, runs each nonempty generated plan family,
and uploads raw results even after a failure. This generic workflow does not
replace the maintained acceptance suite's stronger evidence verification.

## Cleanup and caches

Cluster and build-helper cleanup belongs to Sieve's owning process. Workflows do
not unconditionally delete the shared `kind` cluster or prune the Docker daemon.
Existing clusters are refused, owned resources are cleaned up on failure and
cancellation, and images/build caches remain available for reuse. Persistent
runner cache maintenance is a separate administrator operation.
