# Oracle behavior contracts

Captured from main revision 9d8f4ee50d60abc8ae25c06cbc5019c3e701ee7b before
refactoring artifact loading, comparison or result serialization. The four gzip
JSON documents contain inputs and reviewed expectations for learned state,
event counts, field masks, controller-family exclusions, exact verdicts and
saved result fields. They are compared in CI, never regenerated there.

The two Secret Generator groups retain 24 test outcomes from the successful
Oct 24 run and the preceding run with two incomplete missed-state schedules.
Generated credential data is replaced consistently with numbered fixture values.
Already-masked annotations and managed fields are removed. Logs retain the
lines selected by existing validation and panic checks; unused custom history
is empty. Original experiment artifacts are unchanged.

The small operand cases cover missing/extra resources and fields, ownership,
list ordering, type changes, event counts, panics, workload errors, incomplete
injections and nonzero process outcomes. The learning group covers differing
fields, list positions, baseline resource identities and controller families.
Existing limitations, including how learning treats type changes, are frozen.
Plans are rendered with sorted YAML keys so fixture JSON member order cannot
change the saved plan text.

Read a document with `gzip -dc PATH`. Do not refresh these expectations to make
an implementation change pass; review any verdict or masking change separately.
