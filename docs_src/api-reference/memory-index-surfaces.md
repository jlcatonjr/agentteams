# `memory_index_surfaces` — the memory index across a project's surfaces

> *Source: `agentteams/memory_index_surfaces.py`*

An instance that runs on several providers keeps one copy of `references/memory-index.json` under each agents
directory. Each copy used to be refreshed only by its own provider's update, so they drifted apart: baseAgent's three
copies were built from 77, 103 and 103 of 110 sources (P5, 2026-11-02). This module holds the pieces the retrieval
commands share:

| Name | Purpose |
|---|---|
| `resolve_brief(project_root)` | The project's own brief, found by the canonical `fleet._resolve_descriptor` (`.agentteams/brief.json`, `brief.json`, `.github/agents/_build-description.json`). A symlinked brief, one outside the project, or one whose `existing_project_path` names another project is not used. `--query-index`, `--refresh-index` and `--query-code` use it when no `--description` is given; a read-only query also uses it when the `--description` given doesn't exist (the agents' fenced `.agentteams/brief.json`), but `--refresh-index` never swaps in a brief nobody named. |
| `surface_dirs(project_root)` | Each registered framework's agents directory, from `registry.agents_dir_for` (no second framework→directory table). |
| `index_surfaces(project_root, frozen)` | Every agents directory holding an index, inside the project and not owned by a surface in `frozen_surfaces`. `--refresh-index` rebuilds them all; `--query-index` falls back to the first when the selected surface has none. |
| `surface_framework(agents_dir, project_root)` | The framework a rebuilt copy keeps: the index's own stamp, else the directory's. |
| `extra_files(manifest, project_root)` | The `memory_index_extra_files` sources: named `.md` files inside the project. |
| `coverage_notices(manifest, project_root, sources)` | The build-time report: a file entry or an empty match under `memory_index_extra_dirs`, each folder or glob entry there (it indexes, and commits snippets of, every later file), and Markdown under `Projects/` or `docs/` the index leaves out. |

`--refresh-index` rebuilds this surface's index only when it has one, when `--output` named it, or when no surface has
one yet, so a bare refresh never creates an index for a provider the project doesn't use. It prints each copy's result
and exits 1 when any copy failed (the copies then disagree until the next refresh).

See [`memory_index`](memory-index.md) for the index itself and the [CLI reference](../cli-reference.md) for the flags.
