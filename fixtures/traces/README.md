# Trace behavior fixtures

The expectations were captured at main revision
2824240fd9727d92cccee2a54bee633a0d5c13f9 before changing the parser or event model.
They record decoded fields, operation intervals, graph vertices and edges,
derived differences/counters, and the complete generated plan contents.
Compression and relaxed reconciliation boundaries are exercised independently.

`secret-generator` and `rabbitmq` derive from the retained, successful VM learning
runs. Credential payloads are replaced with numbered fixture values consistently
throughout each trace and oracle. Metadata annotations and managed fields are
removed; these are already masked by the harness. Ordinary log lines become blank
lines, retaining their positions. Original VM evidence is unchanged.

`interleaved` exercises out-of-order IDs, different reconcilers running together,
Get/List reads, null and missing objects, trivial reconciliation, writes outside
a recognized reconciler and operations interrupted at the end of a trace.
`annotated` records standalone annotated writes and their existing filtering.
The in-reconciler annotated-write path currently fails because its event lacks
`set_range`; the refactor must keep that unsupported path explicit. Enabling it
requires a separate behavior change. The existing decoded `from_cache` flag is
also retained; the old decoder passes booleans to a string comparison.

Trace and expectation files are gzip-compressed to keep repeated Kubernetes
payloads small. Read one with `gzip -dc PATH`. CI only compares expectations;
it does not regenerate them.
