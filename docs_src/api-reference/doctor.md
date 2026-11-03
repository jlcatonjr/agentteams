# `doctor` — do the project's providers agree?

> *Source: `agentteams/doctor.py`*

`--check` asks whether one team matches its templates. `--doctor` asks the cross-surface question: do all of the
project's providers see the same rules, the same memory and the same agents? It writes nothing and reports:

| Section | Level | What |
|---|---|---|
| Rules projection | FAIL | A Project-Specific Rules copy or notes pointer that is missing, out of date or refused ([`rules_projection`](rules-projection.md)). |
| Memory index | WARN | Surface copies built from different sources (`source_fingerprint`) ([`memory_index_surfaces`](memory-index-surfaces.md)). |
| Copilot body limit | FAIL | Operator notes past 30,000 characters ([`copilot_body_limit`](copilot-body-limit.md)). |
| Agents | INFO | Agents missing on a surface, notes that differ by surface ([`surface_matrix`](surface-matrix.md)). |
| Shared AGENTS.md | WARN | A root `AGENTS.md` whose `constitutional_core` is older than the other entry files' (frozen with Codex while they moved on). |

`run_doctor(project) -> int` prints the report for `--project` (else the working directory), using the brief found
there for `project_rules_source` and `frozen_surfaces`. It returns 1 when any FAIL is reported.
