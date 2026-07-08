# `code_index` — AgentTeamsModule

Build and query a **vector-space retrieval index over repository scripts and the
external APIs they use** — the code-retrieval sibling of [`memory_index`](memory-index.md).

Where `memory_index` covers durable *prose* (work summaries, CHANGELOG, plans),
`code_index` covers *code*: the repository's own scripts (`source_kind =
local-script`), the external API modules those scripts import (`api-module`), and
the documentation for those APIs (`api-doc`). Every indexed document carries a
`source_kind` label, so a query can be filtered to one kind with `--code-kind`.

> *Source: `agentteams/code_index.py`*

---

## Design Principles

- **Gitignored local cache, never committed.** The index lives under
  `references/code-index/` as a `manifest.json` plus per-kind partition files
  (`local.json`, `api-modules.json`, `api-docs.json`). It is a rebuildable cache
  — not committed, not drift-tracked, and **never staged by a git hook**. The
  `api-*` partitions embed machine-specific resolved paths and installed
  versions, so committing them would leak per-developer state; keeping the cache
  local sidesteps that entirely.
- **Stdlib-only sparse TF·IDF vector space.** The default representation is a
  sparse tf·idf vector-space model (BM25 lexical + cosine vector strategies) —
  the same class `memory_index` uses. No dense embeddings, no FAISS/chroma, no
  heavy dependencies. `vector_model_id`/`vector_dim` are reserved (null) for an
  optional future dense tier.
- **Own copy of the scorers, parity-tested.** The BM25/cosine math is a
  deliberate, behaviour-identical copy of `memory_index`'s scorers (the shipped,
  grid-tuned module is left untouched); `tests/test_code_index.py::test_scoring_parity_*`
  proves the copy cannot silently drift.
- **Code-aware tokenizer.** Unlike the prose tokenizer, it keeps short
  identifiers (`os`, `re`, `id`), keeps dotted import paths whole *and* split,
  and splits `snake_case`/`camelCase` so a query for `batchUpdate` matches
  `batch_update`.
- **Never executes third-party code.** API modules/docs are read via static
  `ast` parsing and `importlib.metadata` only — never `import`, never
  `find_spec`. Correctness (deps may be uninstallable/side-effecting) and
  security (RAG/vector poisoning; treat retrieved API docstrings as data, not
  instructions).
- **Robust to absence** — an empty source set yields an empty-but-schema-valid
  partition; a missing/stale/malformed cache never blocks a query
  (`fallback_policy = non-blocking-file-read-then-search`): open the referenced
  file, then fall back to filesystem search.
- **Deterministic, atomic** — documents are sorted by `(source_kind, path,
  symbol)`; a no-op rebuild is content-fingerprinted and skipped so the on-disk
  partition stays stable; writes are atomic (`*.tmp` + `os.replace`).

---

## CLI

```
# Rebuild the gitignored cache (references/code-index/)
agentteams --refresh-code-index --description brief.json      # or --self

# Query it (auto-refreshes a stale local partition first)
agentteams --query-code "where is the retry backoff" --description brief.json
agentteams --query-code "http client session" --code-kind api      # API modules only
agentteams --query-code "authentication flow" --code-query-strategy vector --code-query-k 8
```

- `--code-kind {local,api,doc,all}` — filter by `source_kind` (default `all`).
- `--code-query-strategy {lexical,vector}` — `lexical` BM25 (default, best for
  identifiers) or `vector` cosine.

The `/code-recall` skill wraps `--query-code` for in-session retrieval and is the
code sibling of the `/recall` (memory-index) skill.

---

## Auto-update triggers

1. **Query-time staleness (primary).** `--query-code` rebuilds a stale partition
   before answering. The `local` partition is stale when any source file's
   hash/mtime changed; the `api-*` partitions are stale when the **dependency
   fingerprint** (dependency-manifest contents + external import-name set +
   dist→version map) changed — because API source lives in site-packages and a
   local mtime cannot witness a dependency upgrade.
2. **`--update`** keeps an *existing* cache fresh (never imposes the build cost on
   a project that never queried it).
3. **Optional pre-commit warm-up** (`--install-git-hooks --code-index-hook`,
   off by default) pre-warms the gitignored cache on commit without staging it.

---

## Domain boundary

`code_index` (code + API) is a distinct retrieval surface from `memory_index`
(durable prose) and from any project-level relational retrieval-integrator
contract — the three address different questions and must not be conflated.

---

## Public API

- `build_code_partition(units, *, source_kind, project_name="", framework="", dependency_fingerprint=None) -> dict`
- `local_units(paths) -> list[dict]`
- `query_partition(partition, query, *, k=5, strategy="lexical") -> list[dict]`
- `query_partitions(partitions, query, *, k=5, strategy="lexical", kind="all") -> list[dict]`
- `is_partition_stale(partition, sources=None, *, dependency_fingerprint=None) -> bool`
- `dependency_fingerprint(manifest_texts, import_names, dist_versions) -> str`
- `partition_content_fingerprint(partition) -> str`
- `build_manifest(partition_meta, *, ...) -> dict`
- `atomic_write_json(path, obj) -> None`

See `schemas/code-index.schema.json` for the artifact contract and
`references/plans/code-api-vector-index.plan.md` for the audited design (v3).
