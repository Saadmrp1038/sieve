# Metadata lifetime histories and observed delivery overlap

External Secrets and cert-manager now share bounded metadata histories, cleanup
and history-equivalence monitors, and an independent delivery-overlap verifier.
This implementation has local TDD coverage. It has not been run in a new VM
campaign, and it establishes no new operator defect. The preceding campaign's
nine-workload/two-fault allowance is exhausted; this slice launches no workloads
or faults and changes no controller reconciliation code or deployment workers.

## Histories and workloads

The catalog contains eight legal histories, each starting with an absent
`sieve.testing/history` entry. Four histories apply to labels and four to
annotations: add; add/change; add/remove; add/change/remove. Each contains at most
three updates. `sieve.testing/keep=retained` remains declared in both maps so that
removal exercises an entry's lifetime without deleting unrelated entries.

Both operator profiles register these workloads:

| Workload | Default history | Schedule |
| --- | --- | --- |
| `metadata-labels-settled` | absent → one → two → absent, label | Await each update's semantic convergence |
| `metadata-labels-overlap` | Same label history | Await add, submit change/remove without convergence waits |
| `metadata-annotations-settled` | absent → one → two → absent, annotation | Await each update's semantic convergence |
| `metadata-annotations-overlap` | Same annotation history | Await add, submit change/remove without convergence waits |

The add is always observed on the child before proceeding. This establishes
that the entry was managed before cleanup is requested. Overlap mode may skip
the intermediate changed value; acceptance of that update does not prove the
operator reconciled it. There are no automatic retries or extra fault actions.
`--history-index` selects another catalog entry in the workload's channel:
labels use indexes 0–3 and annotations 4–7. A catalog is candidate coverage;
each workload executes one selected history.

The adapters mutate `/spec/target/template/metadata` in External Secrets and
`/spec/secretTemplate` in cert-manager. External Secrets uses the credential-free
Fake provider and validates the actual Secret marker; cert-manager uses a
SelfSigned Issuer and validates TLS material, hostname, SAN and matching key.
The existing pins and ten-worker deployment flags are preserved.

The pinned contracts support removing previously managed metadata: see
[External Secrets' template metadata implementation](https://github.com/external-secrets/external-secrets/blob/fc0dd82da6422c6ec840d24425bbda68e5259aab/pkg/controllers/externalsecret/externalsecret_controller_template.go)
and [cert-manager's SecretTemplate API contract](https://github.com/cert-manager/cert-manager/blob/925c22283de556172d48d96a165dc2fa27f7a6ff/pkg/apis/certmanager/v1/types_certificate.go).
The monitored keys avoid operator-reserved metadata.

## Semantic obligations

The runner requires fresh subject/reference parent and child names. It creates
one isolated Fake Store or SelfSigned Issuer and refuses a preexisting dependency.
The fresh reference shares this dependency, whose identity and spec are checked
again before reference creation. API updates use a UID and previous-spec-tested
JSON patch. Each accepted response must establish the requested spec, generation,
resourceVersion and phase. Raw command output is retained even on rejection.

`LatestStateMonitor` retains the existing identity, input, controller ownership,
current-generation readiness and effective-value checks. `MetadataCleanupMonitor`
checks required values and absence of removed keys. Metadata reconciliation can
lag Ready; a metadata mismatch remains pending until the bounded convergence
deadline, when a remaining mismatch is a violation. Unavailability without a
confirmed semantic violation is a convergence failure, not an operator defect.

After the subject reaches its final state, the runner creates a separate fresh
parent declaring that state directly. `HistoryEquivalenceMonitor` compares
validated effective value, Secret type and the explicitly monitored metadata
keys. Each child must be controlled by its own parent. UIDs, object names,
timestamps, resourceVersions, revision counters, unrelated metadata and random
certificate bytes do not participate in equivalence. Both paths must satisfy
the absolute semantic obligations as well as agree with each other.

The final state must remain satisfied across fifteen seconds of samples on one
Ready main-controller Pod UID. A replacement UID resets the stability interval.
Observed Pods must declare the exact ten-worker argument. This does not measure
the runtime number of busy workers; `num_workers: 0` remains the separate Kind
node-topology setting. Convergence timeout defaults to 180 seconds per wait.

## Preparation and artifacts

Plan-only mode contacts no cluster. Use a fresh directory for every selection:

```sh
uv run --locked python controllers/external-secrets/workloads/workload.py \
  metadata-labels-overlap /tmp/new-metadata-case/workload.log --plan-only
```

Substitute cert-manager or another registered workload as appropriate. This
writes `metadata-plan.json` and `metadata-no-fault.yaml`. Normal execution writes
the frozen catalog/selection, `metadata-observations.json`, `semantic-outcome.json`
and raw process journals under `metadata-commands/`. Existing output artifacts
are refused; a copied Sieve execution YAML is preserved as an input. Input objects,
accepted update order/times, parent/child snapshots, main-controller Pods,
monitor verdicts and errors remain available on failure. No cleanup deletes the
subject, reference, dependency or evidence during the workload.

## Verifying overlap

Overlap is independently reviewed after collecting the complete result tree and
verifying every file against its VM SHA256 manifest. Do not inspect a running
stage's logs; only its service state and completion marker may be checked.

```sh
uv run --locked python -m sieve_tool.testing.sequence_coverage \
  /path/to/verified-case/sieve-server.log \
  --observations /path/to/verified-case/metadata-observations.json \
  --output /path/to/new-review/metadata-coverage.json
```

The output directory must exist and the output file must be new. The reviewer
binds input hashes and reports exact source positions for this required order:

1. An explicitly identified reconciliation begins.
2. That invocation gets an older acknowledged parent input.
3. The full informer begins delivery of a newer acknowledged input with the same
   parent UID, kind, namespace/name, exact spec, generation, phase and resourceVersion.
4. That same delivery completes.
5. The same reconciliation and controller instance ends afterward.

Legacy/missing identities, wrong inputs, another observer, missing delivery/end,
wrong ordering and observer errors cannot certify overlap. A zero exit status
means at least one such witness was verified; one means no proof or invalid
evidence. This is an overlap verdict, separate from application health, raw
differential alarms and physical fault verification. A fast update loop, Ready
Pod, success marker or ten configured workers is insufficient.

This proves **input delivery during an invocation that read an older input**.
It does not prove the API acknowledgement happened after invocation start,
simultaneous execution of one workqueue key, or overlap between two parent keys.
Learning images emit the required invocation-bearing trace. Test-mode tracing
is now available through the opt-in `coverageResources` plan field. It accepts
up to sixteen unique exact resource/namespace/name keys. Newly generated metadata
control plans select their subject parent. Rebuild the test operator image to
include the new reconciliation wrappers and client hooks; older test images do
not provide this evidence. Existing plans omit the field and retain their
observation behavior. Receipt pairing and invocation identities are independently
checked by the coordinator, without changing the fault triggers or releasing a
paused read. See [test-mode coverage](test-mode-sequence-coverage.md).

Missing proof remains a coverage limit. The runner records `not_reviewed` for
overlap until the separate reviewer runs; semantic success is not overlap proof.

No stronger crash, partition, lifecycle deletion or multi-key scheduling was
added here. Fresh native controls and any subsequent faults need a separately
bounded campaign. A candidate failure needs raw-evidence review, reproducibility
and an upstream novelty check before it can be called a previously unknown bug.
