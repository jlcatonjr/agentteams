# `memory_index_surfaces` — the memory index across a project's surfaces

> *Source: `agentteams/memory_index_surfaces.py`*

An instance that runs on several providers keeps one copy of `references/memory-index.json` under each agents
directory. Each copy used to be refreshed only by its own provider's update, so they drifted apart: baseAgent's three
copies were built from 77, 103 and 103 of 110 sources (P5, 2026-11-02). This module holds the pieces the retrieval
commands share:

| Name | Purpose |
|---|---|
| `SURFACE_DIRS` | Each framework's agents directory and the framework an index there is stamped with. |
| `BRIEF_CANDIDATES` | Where a brief is looked for, in order: `.agentteams/brief.json`, `brief.json`, `.github/agents/_build-description.json`. |
| `resolve_brief(project_root)` | The first candidate that exists. `--query-index`, `--refresh-index` and `--query-code` use it when no `--description` is given or the one given is missing, so the recall skills' bare commands and the agents' fenced `--description .agentteams/brief.json` both work. |
| `index_surfaces(project_root)` | Every agents directory holding an index. `--refresh-index` rebuilds them all; `--query-index` falls back to the first when the selected surface has none. |
| `surface_framework(agents_dir, project_root)` | The framework a rebuilt copy keeps: the index's own stamp, else the directory's. |
| `extra_files(manifest, project_root)` | The `memory_index_extra_files` sources: named `.md` files inside the project. |
| `coverage_notices(manifest, project_root, sources)` | The build-time report: a file entry or an empty match under `memory_index_extra_dirs`, each folder or glob entry there (it indexes, and commits snippets of, every later file), and Markdown under `Projects/` or `docs/` the index leaves out. |

See [`memory_index`](memory-index.md) for the index itself and the [CLI reference](../cli-reference.md) for the flags.
