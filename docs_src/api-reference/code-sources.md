# `code_sources` — AgentTeamsModule

Collectors for the [`code_index`](code-index.md) feature's **API** partitions
(`api-module`, `api-doc`). Given the repository's local scripts, `code_sources`
discovers the external APIs they import and resolves those APIs' source and
documentation — **without ever executing third-party code**.

> *Source: `agentteams/code_sources.py`*

---

## The hard constraint: never execute third-party code

All resolution is static. `code_sources` uses only:

- `ast` parsing (of local scripts and, when resolved, of API source files), and
- `importlib.metadata` reads — `packages_distributions()`, `metadata()`,
  `version()`, `files()`, and `direct_url.json`.

It **never** calls `import` / `__import__` / `importlib.util.find_spec`, any of
which could trigger a dependency's import-time side effects or a PEP 660
editable-install finder. `tests/test_code_sources.py::test_collect_api_never_imports_third_party`
guards this by making those calls raise if invoked.

Rationale: a dependency may be uninstallable or side-effecting (correctness), and
indexed API content is untrusted (security — RAG/vector poisoning; a retrieved
docstring is *data*, not an instruction).

---

## Pipeline

1. **`extract_imports(py_text)`** — static top-level imports of a script;
   relative imports are skipped (they are local, not external).
2. **`classify_external(names, project_root)`** — an import is *external* when it
   is neither stdlib (`sys.stdlib_module_names`, advisory against the repo's
   `requires-python`) nor a repo-local module.
3. **Resolve metadata-only** — import-name → distribution via
   `packages_distributions()`; source files via `files()`; editable installs via
   `direct_url.json`. Anything unresolvable (namespace packages, zipped eggs,
   distro-stripped installs) degrades to a **declared-only** unit (name +
   version), never an error — the honest partial.
4. **`api-doc` first (robust)** — from `*.dist-info/METADATA`, present even for
   editable/namespace installs.
5. **`api-module` best-effort** — public top-level symbols (functions/classes not
   starting with `_`) with AST-derived signatures + docstrings, **bounded** by
   `_MAX_API_MODULE_FILES`, `_MAX_API_SYMBOLS_PER_MODULE`, `_MAX_BYTES_PER_API_FILE`.

## Dependency fingerprint (api staleness)

`collect_api` records a **dependency fingerprint** = sha256 of (dependency-manifest
contents + external import-name set + dist→version map). The api partitions are
detected stale when this changes — so a dependency upgrade or a newly-added
import triggers a rebuild even though no local file changed (a local mtime cannot
witness a site-packages change). `compute_dependency_fingerprint()` computes the
same value cheaply (no source reads) for the query-time staleness check.

---

## Public API

- `extract_imports(py_text) -> set[str]`
- `classify_external(import_names, project_root) -> list[str]`
- `collect_api(local_sources, project_root) -> ApiCollection`
- `compute_dependency_fingerprint(local_sources, project_root) -> str`
- `dependency_manifest_texts(project_root) -> list[str]`
- `ApiCollection` — `external_imports`, `api_module_units`, `api_doc_units`,
  `declared_only`, `resolved_source`, `dependency_fingerprint`, `declared_only_rate`.

See [`code_index`](code-index.md) for the index this feeds and
`references/plans/code-api-vector-index.plan.md` for the audited design (v3).
