# Instrumentation fixtures

Each input models a source shape used by the current integrations. The Learn and
Test snapshots record the inserted hooks and their order. The test compiles each
rewritten file against the actual `go/client` module and runs its behavior test
offline, covering return values, errors, mutation and event ordering.

Run `go test ./...` normally. After reviewing an intentional change to the emitted
code, update snapshots with `go test -update ./...` and review the resulting diff.
The initial snapshots were captured before modifying the source writer; atomic
file replacement must not change the emitted code. These fixtures do not claim
support for arbitrary Kubernetes or controller-runtime source layouts.

`modern-informer/behavior_test.go.txt` runs against client-go v0.36.2, v0.37.0 and
v0.37.1, the native cert-manager, CloudNativePG and External Secrets dependencies.
Its test invokes the instrumenter
command on a fresh copy of that module and uses public informer APIs plus an
RPC listener. It checks initial-list and live receipt boundaries, and an actual
held/released NotFound read after the final object is deleted. Atomic batching
keeps the upstream defaults. This contract test may download the pinned module;
the fixture compilation and execution have a ten-minute overall deadline.
