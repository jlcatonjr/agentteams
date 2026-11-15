# `emit_report`: the run's printed reports

*Module:* `agentteams/emit_report.py`. It was carved from [`emit`](emit.md) to keep that module under the CH-07 line
ceiling (agentteams #508). `emit` re-exports both functions, so existing callers keep working.

| Function | Purpose |
|---|---|
| `print_dry_run_report(result, manifest, *, fmt="text", stream=None)` | Print a `--dry-run` report as text or JSON. In JSON mode the caller passes the real stdout. |
| `print_summary(result, manifest)` | Print the generation summary: counts, legacy-skip and shrink notices, manual placeholders, cross-reference warnings, and the [agent runtime](agent-runtime.md) lines naming every model or effort value a framework does not write. |
