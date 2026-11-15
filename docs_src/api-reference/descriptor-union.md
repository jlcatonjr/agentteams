# `descriptor_union` — a re-render never silently loses the roster

> *Source: `agentteams/descriptor_union.py`*

One team can have two descriptors. The brief (`.agentteams/brief.json` or the root `brief.json`) carries the content:
authority sources, style rules, components and output paths. The stub (`.github/agents/_build-description.json`) often
carries the roster. When the description has no `selected_archetypes`, `analyze` re-runs keyword selection, and that
can drop agents. The fleet takes the brief whole whenever one exists, so a researchteam-derived repository lost agents
on every fleet re-render (IM item 12, G-17-1).

The fix runs where an `--update` or `--check` run loads the description. That covers the fleet and a hand-run
`--update --description brief.json`, writes no temp file, and keeps the real descriptor path in every record.

| Name | Purpose |
|---|---|
| `ROSTER_FIELDS` | The roster keys a stub can carry: `selected_archetypes`, `governance_agents`, `write_policy`, `retrieval_integration`. Only `selected_archetypes` is applied. |
| `FORCE_APPENDED` | Archetypes `analyze` adds from a capability (`retrieval-integrator`, `research-analyst`, `agentteams-updater`, `vuln-remediation`). Turning the capability off removes them, which is not a roster loss. |
| `stub_for(project, output, description)` | The project's stub. With `--project`, only that project's stub counts, so an example brief rendered into another project never brings its own stub's roster. Without it, the places `build_team._check_dual_descriptor` probes from `--output` (which is often the agents directory itself), then the one beside the description's project. A symlink or non-regular file never counts, and neither does the description itself. |
| `apply_stub_roster(description, args)` | On `--update` and `--check` runs only (a fresh render keeps keyword selection), copies the stub's `selected_archetypes` into a description that has none, and prints where they came from. A roster with an item that is not a known archetype (a string with a `templates/domain/<slug>.template.md`) is not applied and counts as unusable. It reports the other roster keys the stub sets and the description lacks, but never applies them: they change capability and governance, and an operator may have deleted one from the brief on purpose. It records what it found on `args` for the guard. |
| `dropped_archetypes(manifest, stub_path)` | The stub's `selected_archetypes` that the manifest no longer selects, excluding `FORCE_APPENDED`. |
| `refuse_roster_shrink(args, manifest, project_root)` | Called by `--update` before anything is written. It refuses under `--yes` only when the roster cannot be known: the description has none and the stub exists but cannot be used (unparseable, or a roster with an unknown archetype). Without `--yes` it only warns, and `--allow-roster-shrink` accepts the run. A description that sets its own roster is authoritative, so archetypes it leaves out are reported, not refused, and a stale stub never fails a run. |

researchteam's `_update_cmd._resolve_descriptor` makes a similar union, but lets the stub's roster override a brief
roster that differs. Here the description wins on every key it sets.
