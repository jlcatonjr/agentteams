| Category | Features | Description |
|----------|----------|-------------|
| **Core Pipeline** | 39 | Project ingestion, team generation, framework adapters, CLI, templates, schemas |
| **Enrichment** | 11 | Auto-enrichment, notebook scanning, tool catalog, live PyPI metadata |
| **Section Fencing & Safe Merges** | 5 | `AGENTTEAMS:BEGIN/END` markers, non-destructive merge mode |
| **Security Intelligence** | 6 | Live CVE/CISA-KEV/EPSS data, credential scanning, security reference files |
| **Migration & Update** | 8 | `--migrate`, `--revert-migration`, `--enrich`, `--auto-correct`, `--scan-security`, security flags, multi-workspace fleet update (`--fleet`) |
| **Safety & Backups** | 6 | Automatic backups, restore capability, `--no-backup`, `--list-backups`, public backup API |
| **Governance Agents** | 11 | navigator, security, code-hygiene, adversarial, conflict-auditor, conflict-resolution, cleanup, agent-updater, agent-refactor, repo-liaison, git-operations |
| **Workflows** | 14 | Workflows 1–12 with constitutional rules, guards, and final-check termination |
| **Governance Infrastructure** | 12 | `@agent-updater` auto-triggers, `@adversarial` guards, truth check, drift-as-trigger, deployment protocol |
| **Interoperability** | 8 | `convert`, `interop`, `bridge` modules; handoff manifest; CI flags |
| **Bridge Automation** | 7 | Daily maintenance script, CI workflows, staleness watchdog, deduplication |
| **Cross-Repository Support** | 7 | `@repo-liaison` agent, adjacent-repo tracking, impact/update/coordination protocols |
| **Drift Trust & Delivery Gating** | 9 | Structural drift diff, delivery receipts, behavioural-drift detection, trust gating |
| **Retrieval & Review-Time Utilities** | 3 | Memory index, code & API index, session scan |
| **Feature Audit** | 5 | Machine-readable feature registry, per-tier proof enforcement, reachability classification, end-to-end CLI probes |

**Total:** 151 documented features across 15 capability areas — generated from `references/feature-registry.csv`, which is itself derived from the body of `feature-inventory.md`. Checked by `tests/test_feature_registry.py::test_the_summary_table_is_generated_from_the_registry`. It read `125 across 12` until 2026-08-07, having silently omitted two whole capability areas and undercounted the rest by nine: the only check compared the total to *its own column*, so a table that agreed with itself and with nothing else passed.
