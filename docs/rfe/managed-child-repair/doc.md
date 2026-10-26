# Managed-child repair workloads

## First milestone

Add focused workloads that disturb a managed child while leaving its custom
resource unchanged. Start with Mittwald Secret Generator v3.5.0, then RabbitMQ
at its existing pinned revision. Keep the existing fault mechanisms and
historical workloads unchanged.

Use manually written operations and operator-specific correctness checks. A
general sequence generator and new API-error injection actions are later work.

## Test boundaries

The user authorized these boundaries on October 26, 2026, after reviewing the
requirements of the repository's TDD skill:

1. The workload command configured by the controller profile. Observe its exit
   status, finish marker, and saved repair evidence. It must distinguish repair,
   absent repair, a changed parent, and an unsuccessful mutation. Local tests
   may replace the external Kubernetes boundary, not workload internals. These
   tests verify the driver, not the real operator's reconciliation.
2. Real `sieve vanilla`, `sieve learn`, and `sieve test` runs on the VM. Observe
   resource state, raw alarms, workload outcomes, and actual restart or held and
   released read evidence. This verifies only the executed target versions and
   schedules; CLI success alone is insufficient.

## Workload contract

Each workload establishes a converged parent and child, records their identities,
and makes one child mutation:

- Delete the child and require a replacement with a different UID.
- Change an operator-controlled field and require its intended value to return.

Run those mutations as separate workloads so a failed first repair cannot hide
the second result. Require the parent's UID, generation, and spec to remain
unchanged. Status and resource versions can legitimately change. A replacement
must belong to the same parent; any status reference must identify that child.

Persist the mutation response and relevant before/after observations. A field
patch response can establish that drift occurred even if repair precedes the
next read. Distinguish baseline setup failure, rejected mutation, unchanged-parent
violation, repair timeout, and successful repair. Keep evidence on failure.

Use finite waits and the harness's existing process/request limits. Do not add
parent updates merely to provoke reconciliation or silently relax assertions
for a fault schedule. Preserve every raw Sieve alarm.

## TDD sequence

Complete one failing test and its minimal implementation before the next slice:

1. Secret deletion: a repairing external system produces successful workload
   evidence with a new child UID and the original parent identity.
2. Missing repair: the command terminates within its configured wait, retains
   evidence, and never writes the success marker.
3. Identity protection: replacement ownership or a changed parent prevents a
   successful verdict.
4. Secret data drift: establish that the mutation was accepted, then require
   restoration under the unchanged parent; add its failed-repair control next.
5. RabbitMQ Service deletion and selector drift: repeat the behavior tests at
   the same workload boundary, then implement the operator-specific checks.
6. Real integration: run the existing workloads as controls and the new
   workloads without faults. Only successful baseline workloads can produce
   learned references and proceed to fault runs.

Tests for a repairing and deliberately non-repairing external system validate
the workload detector. They are synthetic controls, not discovered operator
bugs or substitutes for real VM runs.

## Operator expectations to verify

Secret Generator's pinned StringSecret controller watches StringSecret changes
but does not register an owned-Secret watch. Successful create/update calls do
not schedule periodic reconciliation. Its README describes propagation after
parent creation or updates, without an explicit unchanged-parent repair promise.
Independent Secret deletion or drift may therefore fail even without Sieve
faults. Treat missing desired-state convergence as a credible operator-bug
candidate, and distinguish the observed failure from the inferred cause,
intended contract and novelty. README ambiguity alone does not establish that
the behavior is intentional. Do not patch the operator to make a baseline pass.
The [upstream investigation](secret-generator-upstream.md) checks existing
reports, release versions and an independent published-image reproduction.
The subsequent [maintained-operator study](maintained-operators.md) records
current External Secrets and cert-manager recovery checks and their coverage
limits.

RabbitMQ registers an owned-Service watch and sets the Service selector during
reconciliation. Target the client-facing Service, not persistent volumes or
generated credentials. Verify its ownership, intended selector, and usable
endpoints after repair.

Pinned source references:

- [StringSecret controller](https://github.com/mittwald/kubernetes-secret-generator/blob/f7e44605aa7980c305f0e600de1613e3c0e57efd/pkg/controller/crd/stringsecret/stringsecret_controller.go)
- [Secret Generator client and requeue behavior](https://github.com/mittwald/kubernetes-secret-generator/blob/f7e44605aa7980c305f0e600de1613e3c0e57efd/pkg/controller/crd/types.go)
- [Secret Generator contract](https://github.com/mittwald/kubernetes-secret-generator/blob/f7e44605aa7980c305f0e600de1613e3c0e57efd/README.md#cr-based-generation)
- [RabbitMQ owned-resource watches](https://github.com/rabbitmq/cluster-operator/blob/4f13b9a942ad34fece0171d2174aa0264b10e947/controllers/rabbitmqcluster_controller.go)
- [RabbitMQ Service reconciliation](https://github.com/rabbitmq/cluster-operator/blob/4f13b9a942ad34fece0171d2174aa0264b10e947/internal/resource/service.go)

## Evaluation

Keep the current workloads as the comparison group. Pin operator and Kubernetes
revisions and give both groups matched execution budgets. Report workload
coverage, generated schedules, attempted schedules, effective injections, and
distinct reproducible failures separately. Minimize candidate failures and
validate the detector against a buggy/fixed example before claiming increased
bug-finding capability. Label seeded failures separately from operator defects.

For new workloads that converge without faults, learn two fresh baselines and
run bounded schedules from the existing fault families. An existing schedule
cannot be assumed applicable to a different workload. Record unreachable
triggers and scheduling dependency cycles as integration failures, not operator
bugs. Preserve original plans and disclose any scope amendments.

Use the existing VM and cached images after verifying their build provenance.
Keep new evidence in fresh `runs/` directories; do not overwrite earlier results
or read progress logs continuously. Inspect final evidence after each bounded
batch completes.
