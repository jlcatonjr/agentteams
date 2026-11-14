# `carveout_checks` — the carve-out registry's checks against the team directory and history

> *Source: `agentteams/carveout_checks.py`*

The checks [`carveouts`](carveouts.md) runs before anything binds. They are split into their own module to keep each
under the module-size ceiling, and every one fails closed.

| Name | Purpose |
|---|---|
| `team_dir_problem(root, registry)` | The pinned `decision_log` must be a generated team (`references/build-log.json`), not reached through a symlink, and hold its decisions log and exception registry. |
| `rule15_numbers(root)` / `clause_problems(root, registry)` | The number this project's constitution gives the constraint-relaxing-authorizations rule; only its clause (b) may be lifted, and nothing may when no entry file numbers the rule. |
| `rollback_problem(root, registry)` | Refuses a shallow clone, any version in the registry's last 500 commits (`--full-history`, replace refs ignored) with an equal or higher `sequence`, and a sequence below the team dir's high-water mark. |
| `record_high_water(root, registry)` | Raises `references/carveout-high-water.json` after a successful render. A read-only mark (a sandboxed run: the file is protected since 2026-11-12) is reported, never fatal. |
| `active_carveouts_for(team_dir, *, today=None)` | How many carve-outs count against the team's exception cap: entries with status `active` and an `expires` not past (or unreadable), in the project's registry when it pins this team dir. Used by `exception_registry.assert_within_aggregate_cap` and `--audit-exceptions`. Raises `CarveoutError` for an unreadable registry. |
| `check_own_halts(root, registry, entries)` | A HALT on `carveout:<id>` stops that carve-out (C-2). |
| `check_lineage(root, registry, entries)` | Each lineage id must be a logged decision with no unretracted HALT, ancestors included. |
| `check_clearances(root, registry, entries)` | A declared `clearance` must have a verified PASS bound to its file's sha256 (`cli/clearance_binding.py`). |
| `check_aggregate_cap(root, registry, entries)` | Every active carve-out plus the team's active relaxing exceptions, against the Rule 15 cap. |

Each returns `Problem` records with stable codes, and `carveouts.binding_entries` collects them.
