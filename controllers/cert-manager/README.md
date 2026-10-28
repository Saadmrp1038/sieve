# cert-manager v1.21.3

This profile pins `925c22283de556172d48d96a165dc2fa27f7a6ff` and retains native
client-go v0.36.2, controller-runtime v0.24.1 and Go 1.26.10. The main controller
is built with all issuer/provider registrations. The upstream Containerfile,
licenses and pinned distroless base image are retained. The binary submodule
receives the same local client-go/Sieve module replacements as the root module;
operator Go dependencies are not upgraded.

## Scope

Learning observations bracket the six Certificate controllers (trigger,
keymanager, requestmanager, issuing, readiness and revisionmanager) and the
namespaced Issuer controller. They use distinct ProcessItem stack frames.
The shared CertificateRequest issuer/approver implementations are not annotated
in this first profile: multiple issuer instances share those methods. They
remain enabled, and their API changes remain visible in API-server evidence.
This limits generated scheduling coverage of their internal reads/writes.

All default controllers and the default five workers per controller remain
enabled. Each workload creates one Certificate, so this is not a concurrent
multi-Certificate evaluation. Sieve's existing one-active-worker-per-annotated-
reconciler trace limitation remains; an overlapping trace is a harness failure.
No reconciliation rule, provider registration or workqueue policy is changed.

The main controller runs in `default` for Sieve's pod/action support. Its service
account and binding subjects are adapted accordingly. Webhook and cainjector
remain in `cert-manager` using the official images. Cluster resource secrets
retain the release's `cert-manager` namespace. The supported non-default
`--enable-certificate-owner-ref=true` flag enables the existing ownership check.

## Workloads

`delete-child` and `drift-child` reuse the existing SelfSigned Issuer/ECDSA
Certificate repair checks, relocated from `experiments/active-operators`.
Deletion has a UID precondition and requires a new Secret UID. Drift uses a
UID-tested patch of `tls.crt` and requires valid TLS material to return.

Both require unchanged parent UID/generation/spec, current-generation Ready,
correct Secret ownership, certificate validity/hostname/server usage, matching
certificate/key public keys and 15 seconds of stable repair. Waits default to
180 seconds each. The mutation response and before/after evidence are preserved
on failure. These are test certificates; no cloud credentials are required.

```sh
uv run --locked sieve build -c cert-manager -m all -r sieve-lab
uv run --locked sieve vanilla -c cert-manager -w delete-child -r sieve-lab
uv run --locked sieve vanilla -c cert-manager -w drift-child -r sieve-lab
uv run --locked sieve learn -c cert-manager -w delete-child --build-oracle -r sieve-lab
uv run --locked sieve learn -c cert-manager -w drift-child --build-oracle -r sieve-lab
uv run --locked sieve test -c cert-manager -p GENERATED_PLAN -r sieve-lab
```

Use validated Kubernetes v1.36.4 learn/test node images with the same registry.
The original release manifest is downloaded by immutable version URL and checked
against SHA256 before server-side apply. The main deployment is adapted from
that same checked release. Only successful controls may produce learned oracles.

The native client-go command/informer tests cover v0.36.2 and v0.37.1. Real runs
must additionally verify trace observations, every physical injection, accepted
mutations, unchanged parents, stable TLS repair, CA capture and all raw alarms.
A generated plan or completed coordinator alone is insufficient. Unreached
triggers, circular pauses and zero plans remain coverage limitations.

The [VM validation report](../../docs/rfe/managed-child-repair/cert-manager-sieve-validation.md)
records 49 attempted schedules, 20 verified restarts and zero completed
missed-state injections, including every raw alarm and the remaining coverage
limitations. The [injection-fix validation](../../docs/rfe/managed-child-repair/cert-manager-injection-fixes-validation.md)
records all 15 mapped restart replacements and 14 missed states physically verified,
with the original failures, diagnostic replays and raw alarms preserved.

## Replacement schedules for incomplete injections

This profile now opts into `write_trigger: occurrence`. Restart predicates count
successful create/update/delete notifications per reconciler and selected resource,
including all update verbs before candidate pruning. They do not compare a learned
state difference to a later reconciliation's lister snapshot. Other profiles retain
state-difference predicates. `generated_name_prefixes` explicitly selects
`secret/default/sieve-certificate-*`; `resourceKeyPattern: true` in a generated
condition enables that name-prefix selector. Resource type and namespace stay exact,
and the default remains literal matching. This also removes random temporary
Secret names embedded in Certificate status differences from these predicates.
Changing the predicate changes the schedule; retain original YAMLs and record each
replacement's source, hash, selected write and physical outcome separately.

For completed missed states, use `delete-child-miss-{issuer,parent,child}-updates`
or `drift-child-miss-{issuer,parent,child}-updates`. These create the same single
SelfSigned Certificate, wait for Ready with no active Issuing condition, and supply
four UID-tested A/B/C/D patches eight seconds apart without reconciliation waits
between patches. Issuer patches change SelfSigned CRL distribution URLs; parent
patches change declared DNS names; child patches corrupt then restore `tls.crt`.
Each accepted response, input projection, UID and resourceVersion is retained.
Only parent mode intentionally advances Certificate generation/spec; other modes
require them unchanged. After convergence, the existing deletion/drift workload
runs under the final parent, with the same ownership, current-generation Ready,
valid TLS/key and 15-second stability requirements. Parent mode also checks the
final declared hostname; Issuer mode checks the repaired certificate's final CRL
URL. Independent updates are extra input coverage, not the original circular
controller-produced transitions.

The independent-update generator requires baseline intermediate reads and later
accepted receipts in every observed cache stream. It pauses one annotated reader
and resumes when those streams reach the independent overwrite. Verification must
show a real hold/release, no intermediate Get by that reader, and a returned later
accepted value for the same UID. A pause or a coordinator completion alone does
not establish a missed state. Generated candidates lacking this proof remain
incomplete; never add a timeout release to make them pass.
