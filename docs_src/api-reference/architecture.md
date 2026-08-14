# `architecture` — AgentTeamsModule

Repository module-dependency (architecture) map — the code-side sibling of
[`graph`](graph.md) (which maps *agent* topology).

Parses the `import` statements of a Python package via `ast` and builds a
directed dependency graph of the package's own modules. Two views: a
**package-level** diagram — a standalone deterministic **SVG** (referenced by the
Markdown document, with the Mermaid/DOT source kept under a `<details>` block),
inter-package edges only, for a readable high-level architecture — and
**module-level** detail (full per-module adjacency
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

### `generate_architecture_svg(repo_root, package_dir=None) -> str | None`

Build the graph and return the package-level dependency SVG (auto-detecting the
package when `package_dir` is omitted). Returns `None` when no importable package
is found.

### `generate_architecture_module_svg(repo_root, package_dir=None) -> str | None`

Build the graph and return the module-level dependency SVG (auto-detecting the
package when `package_dir` is omitted). Returns `None` when no importable package
is found.

---

### `module_level_edges(package_dir, root_pkg) -> set[tuple[str, str]]`

> *Source: `agentteams/architecture.py`*

Import edges that exist **at module load time** — only `import` statements that are
direct children of the module body.

**Args:**

- `package_dir` (`Path`) — Package directory to scan.
- `root_pkg` (`str`) — Package name, used to keep edges internal and to resolve
  `from pkg import submodule` to the submodule rather than to `pkg`.

**Returns:** `set[tuple[str, str]]` — `(importer, imported)` module-name pairs.

**Why this exists rather than reusing `ArchitectureGraph.edges`:** that graph is built
with `ast.walk`, which cannot distinguish a load-time import from one deferred inside a
function. Cycle detection over it reported three cycles in this package, none
actionable — `analyze`/`output_plan`, `emit`/`fence_inject` and
`cli.commands`/`stale_remediate` are each deliberately broken by deferring one side.
Reading only direct children of the module body gives the true load-time picture.

Unparseable files are skipped with a `warnings.warn` rather than silently — a
best-effort mapper must tolerate a broken source file, but CH-24 forbids swallowing
the fact.

### `detect_import_cycles(graph, *, edges=None) -> list[list[str]]`

> *Source: `agentteams/architecture.py`*

Find import cycles. **CH-13.**

**Args:**

- `graph` (`ArchitectureGraph`) — The module graph.
- `edges` (`set[tuple[str, str]] | None`, keyword-only) — Edge set to analyse. Pass
  `module_level_edges(...)` for load-time cycles; `None` uses the graph's own edges,
  which include deferred imports and so report cycles that never occur at runtime.

**Returns:** `list[list[str]]` — One list of module names per cycle found.

Guarded by `tests/test_living_doc_and_cycles.py::test_this_package_has_no_load_time_cycles`.
Scope: load-time static imports of one package — a deferred, dynamic or third-party
cycle is not modelled.

## CLI

```
python -m agentteams.architecture .                    # auto-detect package
python -m agentteams.architecture . --package agentteams
python -m agentteams.architecture . --format mermaid|dot|json|markdown|svg [-o FILE]
```

Also available as `agentteams --refresh-architecture` (standalone; no
`--description` needed).
