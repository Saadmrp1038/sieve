# Local settings

Create `configs/local.json` with only the harness defaults you want to override:

```json
{"container_registry": "sieve-local", "workload_conditional_wait_timeout": 600}
```

The file is ignored. `sieve --config /path/to/settings.json ...` selects an
explicit override. Unknown fields and mismatched value types fail validation.
The default settings ship in `sieve_tool.resources/defaults.json`.
