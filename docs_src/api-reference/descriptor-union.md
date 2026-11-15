# `descriptor_union` — a re-render never silently loses the roster

> *Source: `agentteams/descriptor_union.py`*

One team can have two descriptors. The brief (`.agentteams/brief.json` or the root `brief.json`) carries the content:
authority sources, style rules, components and output paths. The stub (`.github/agents/_build-description.json`) often
carries the roster. When the description has no `selected_archetypes`, `analyze` re-runs keyword selection, and that
can drop agents. The fleet used to take the brief whole whenever one existed, so a researchteam-derived repository lost
agents on every fleet re-render (IM item 12, G-17-1).

| Name | Purpose |
|---|---|
| `ROSTER_FIELDS` | The keys a stub can supply: `selected_archetypes`, `governance_agents`, `write_policy`, `retrieval_integration`. |
| `union(brief, stub)` | Returns `(merged, injected)`: the brief with each roster key it lacks copied from the stub. The brief wins on every key it sets, and an empty stub value adds nothing. Neither input is modified. |
| `for_fleet(ws, descriptor)` | The descriptor the fleet renders `ws` with. When `descriptor` is a brief and the stub adds a roster key, it writes the union as `brief.json` in a private temp directory and returns that path; otherwise it returns `descriptor` unchanged. Nothing is written into the workspace, dry run included, and the temp directories are removed at exit. The name keeps the fleet's `--no-scan` rule for briefs, and `ingest` resolves no path relative to the descriptor's folder. The fleet report still records the real descriptor. |
| `stub_for(project_root, project)` | The project's stub: `--project`'s first, then under `project_root`, then `project_root` as the agents directory (`--output` is often the agents directory itself). The same candidates as the dual-descriptor advisory. |
| `dropped_archetypes(manifest, project_root, project)` | The stub's `selected_archetypes` that the manifest no longer selects, sorted. Empty when there is no stub or it has no roster. |
| `refuse_roster_shrink(args, manifest, project_root)` | Called by `--update` before anything is written. When archetypes would be dropped, it warns without `--yes`, and refuses (returns True) under `--yes` unless `--allow-roster-shrink` is given. |

researchteam's `_update_cmd._resolve_descriptor` makes the same union, except that its stub roster overrides a brief
roster that differs. Here the brief's own roster is kept, and `refuse_roster_shrink` catches a brief whose roster is
thinner than the stub's.
