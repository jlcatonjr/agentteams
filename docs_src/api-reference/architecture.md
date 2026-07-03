# `architecture` — AgentTeamsModule

Repository module-dependency (architecture) map — the code-side sibling of
[`graph`](graph.md) (which maps *agent* topology).

Parses the `import` statements of a Python package via `ast` and builds a
directed dependency graph of the package's own modules. Two views: a
**package-level** Mermaid/DOT diagram (inter-package edges only, for a readable
high-level architecture) and **module-level** detail (full per-module adjacency
in the JSON block and Markdown dependency table, plus an external-dependency
summary).

Output is a single Markdown document written to
`references/architecture-graph.md`, regenerated on every commit that touches the
package's `.py` files via the pre-commit hook installed by
[`git-hooks`](git-hooks.md).

> *Source: `agentteams/architecture.py`*

---

## Determinism

Every serialiser sorts nodes, edges, and adjacency keys so the output is
independent of filesystem walk order — the same guarantee
[`graph`](graph.md) relies on so the commit-refresh never produces spurious
diffs.

---

## Key functions

### `discover_package_root(repo_root) -> Path | None`

Return the repo's primary importable package directory (a top-level child with
an `__init__.py`; prefers one whose name matches the repo directory). Returns
`None` when there is no importable package.

### `build_architecture(repo_root, package_dir) -> ArchitectureGraph`

Two-pass build: register every module (so import targets resolve), then extract
internal import edges and external (third-party) dependencies. Handles absolute,
`from … import`, submodule, and relative (`.`/`..`) imports; classifies each
imported name as an internal edge, a standard-library import (ignored), or an
external dependency via `sys.stdlib_module_names`.

### `generate_architecture_document(repo_root, package_dir=None) -> str | None`

Build the graph and return the full Markdown document (auto-detecting the
package when `package_dir` is omitted). Returns `None` when no importable package
is found.

---

## CLI

```
python -m agentteams.architecture .                    # auto-detect package
python -m agentteams.architecture . --package agentteams
python -m agentteams.architecture . --format mermaid|dot|json|markdown [-o FILE]
```

Also available as `agentteams --refresh-architecture` (standalone; no
`--description` needed).
