# Plan contract fixtures

`restart.yaml` and `missed-state.yaml` are retained Secret Generator schedules.
The missed-state case includes the previously reviewed Secret-reconciler scope
for plan 7. Both Python and Go load these files. `invalid-cases.json` applies
one mutation at a time to `restart.yaml`; both validators must reject each case.

`generation/cases.yaml` records outputs from the generators at main revision
fc7b6b974e6d167f356a08ee22b6c92fd54dfbb9, before their migration to typed models.
It covers create, delete, update, patch, status writes, annotated calls, all three
missed-state branches and both stale-state pause timings. Tests compare complete
plan contents, including occurrence counts, expressions and optional fields.
Changing these snapshots requires reviewing the resulting fault behavior;
CI does not regenerate them.
