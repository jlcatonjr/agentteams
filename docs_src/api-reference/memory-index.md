# `memory_index` — AgentTeamsModule

Build and query a search index over durable agent team documentation.

Used by `@navigator`, `@adversarial`, and `@work-summarizer` to retrieve relevant work summaries, CHANGELOG entries, and plan artifacts from previous sessions. Supports two retrieval strategies: BM25 lexical (default, high precision for keyword queries) and sparse tf·idf vector cosine similarity (optional, better for thematic/semantic queries). Both are stdlib-only, deterministic, and have no external dependencies.

> *Source: `agentteams/memory_index.py`*

---

## Design Principles

- **Two strategies, no embeddings** — BM25 lexical (default) and sparse cosine vector; no external ML dependencies
- **Durable sources only** — Caller passes explicit source paths; module never globs ignored directories
- **Robust to absence** — Empty source list → empty but valid index (no error)
- **Graceful degradation** — Per-paragraph passage scoring (I2, I9) with backward-compatible snippet fallback
- **Explicit contract metadata** — Build/runtime ownership, fallback policy, and vector runtime mode are encoded in the index payload
- **Deterministic** — Same source set always yields same scores; suitable for testing and reproducible workflows

---

## Constants

### `MEMORY_INDEX_SCHEMA_VERSION`

> *Source: `agentteams/memory_index.py`*

Current schema version for memory index artifacts. Used to detect compatibility between build and consumer versions.

**Type:** `str`  
**Current value:** `"1.3"`

**Schema versions:**

| Version | Change |
|---------|--------|
| 1.1 | Legacy: no per-paragraph storage; single `snippet` field only |
| 1.2 | Per-paragraph passage storage; `paragraphs` list added; `query_index()` returns `snippets` list alongside legacy `snippet` alias |
| 1.3 | Adds explicit contract metadata (`index_format_version`, `index_build_id`, `index_write_owner`, `vector_runtime_mode`, `fallback_policy`, `source_fingerprint`) and per-document `source_hash` |

---

## Functions

### `build_memory_index(sources, *, project_name="", framework="")`

> *Source: `agentteams/memory_index.py`*

Build a BM25 search index over durable text sources.

**Args:**

- `sources` (`Iterable[Path | str]`) — List of file paths to index (work summaries, CHANGELOG, plan artifacts, etc.). Missing or unreadable files are silently skipped.
- `project_name` (`str`, keyword-only) — Optional project name to embed in the index metadata. Default: `""`.
- `framework` (`str`, keyword-only) — Optional framework name to embed in the index metadata. Default: `""`.

**Returns:** `dict[str, Any]` — Index dict with keys:
- `artifact_type`: `"memory-index"`
- `memory_index_schema_version`: Current schema version
- `index_format_version`: Serving-format identifier for the artifact structure
- `index_build_id`: Build fingerprint for this specific index emission
- `index_write_owner`: Writer ownership marker (`agentteams.build_team`)
- `vector_runtime_mode`: Runtime strategy implementation (`sparse-tfidf-cosine`)
- `vector_model_id`, `vector_dim`: Reserved nullable fields for future embedding/vector model metadata
- `fallback_policy`: Declares non-blocking fallback semantics
- `source_fingerprint`: Stable digest across indexed document path/hash pairs
- `project_name`, `framework`: Supplied metadata
- `built_at`: ISO-8601 timestamp
- `source_count`: Number of files supplied
- `documents`: List of indexed documents (with title, path, snippet, paragraphs, length, `source_hash`, etc.)
- `postings`: Term-to-document frequency postings (BM25 substrate)
- `avgdl`: Average document length (for BM25 normalization)
- `N`: Total document count

**Behavior:**

- Empty source list yields a valid empty index (no error).
- Files are read once; I/O errors are silently skipped (robust to in-flight churn).
- Paragraphs are extracted per-document (up to 20 substantive paragraphs per source); each truncated to 480 characters for compact JSON.
- Headings and non-substantive paragraphs are filtered out.
- Stopword list (small, generic English set) is applied during tokenization.

**Raises:**

- No exceptions; I/O failures are silently skipped.

---

### `is_index_stale(index, sources)`

> *Source: `agentteams/memory_index.py`*

Check if any source file is newer than the index's `built_at` timestamp or no longer matches recorded content hashes.

**Args:**

- `index` (`dict[str, Any]`) — Index dict from `build_memory_index()`.
- `sources` (`Iterable[Path | str]`) — Original source paths (same list used for build, or a subset).

**Returns:** `bool` — `True` if any source content hash mismatches indexed `source_hash`, or if any source mtime > index `built_at` (index is stale); `False` otherwise.

**Safety:** Invalid/missing timestamps are treated as stale (conservative). Missing/unreadable files for hashed documents are also treated as stale.

---

### `query_index(index, query, *, k=5, strategy="lexical")`

> *Source: `agentteams/memory_index.py`*

Return the top-*k* documents for *query* using the specified retrieval strategy.

**Args:**

- `index` (`dict[str, Any]`) — Index dict from `build_memory_index()`.
- `query` (`str`) — Free-text search query (e.g., `"behavioral drift drift detection"`).
- `k` (`int`, keyword-only) — Number of top results to return. Default: `5`.
- `strategy` (`str`, keyword-only) — Query strategy. Default: `"lexical"`.
  - `"lexical"`: BM25 term-frequency ranking. High precision for keyword/exact-term queries.
  - `"vector"`: Sparse tf·idf cosine similarity. Better for thematic/semantic queries where multiple concepts must be related. Stdlib-only, deterministic, <100ms at N=180 docs.
  - Raises `ValueError` for any other value.

**Returns:** `list[dict[str, Any]]` — Ranked results (highest score first). Each result dict contains:
- `doc_id`: Internal document ID
- `path`: Source file path
- `title`: Extracted heading (first `# ` line) or filename
- `score`: Relevance score (rounded to 6 decimal places; BM25 for lexical, cosine similarity for vector)
- `snippet`: Best single passage (backward-compat; always present)
- `snippets`: List of up to 3 best-matching passages (new in v1.2; dynamic passage scoring; present even for v1.1 indexes)

**Behavior:**

- Query terms are tokenized and stopwords removed.
- If no query terms match the index, returns empty list.
- Ties are broken deterministically by `(-score, doc_id)` so callers see stable ordering.
- Passage scoring is intra-document only (relative ranking within each document); best paragraphs are ranked independently.
- Backward-compat: v1.1 indexes (no `paragraphs` field) return the stored static `snippet` for all results.
- Both strategies are stdlib-only with no external dependencies.

---

## Query Strategies

The memory index supports two retrieval strategies, each optimized for different use cases.

### Lexical (BM25) — Default

**Algorithm:** Probabilistic information retrieval using term frequency and inverse document frequency.

**Use when:**
- You have specific keywords ("where is the delivery-procedure doc?")
- You want exact-term matches ("when did we add feature X?")
- Speed is the top priority (slightly faster than vector)

**Example:**
```python
hits = query_index(index, "delivery receipt", strategy="lexical")
```

### Vector (Cosine Similarity) — Optional

**Algorithm:** Sparse tf·idf vector-space scoring with cosine similarity. No numpy, no embeddings — stdlib only.

**Use when:**
- Searching for thematic overlaps ("what's our policy on error handling?")
- You want documents related to ALL query terms, not just some
- Generating summaries and need to avoid duplicating prior themes

**Example:**
```python
hits = query_index(index, "memory index performance", strategy="vector")
```

### Strategy Selection

```
Is your query a simple keyword lookup?
  ├─ YES → Use lexical (faster, exact matches)
  └─ NO → Is it a thematic query with multiple concepts?
        ├─ YES → Use vector (semantic matching)
        └─ NO → Use lexical (safe default)

Did your first query return low-confidence results (empty or score < 0.1)?
  ├─ YES → Retry with the other strategy
  └─ NO → Use the result
```

### Performance

Both strategies are fast and have no external dependencies:

| Operation | Corpus Size | Time (ms) |
|-----------|-------------|-----------|
| Build index | 180 docs | <100 |
| Query (lexical) | 180 docs | 20–50 |
| Query (vector) | 180 docs | 40–80 |
| 25 queries | 180 docs | <2500 total |

See `tests/test_memory_index_performance.py` for detailed benchmarks.

---

## BM25 Tuning

Grid search over the AgentTeams N≈10⁵-token corpus (2026-05-19) with 10-document eval set:

| Parameter | Candidates | Selected | Rationale |
|-----------|-----------|----------|-----------|
| K₁ | 1.2, 1.5, 2.0 | 1.5 | Textbook default; no differentiation in eval |
| B | 0.75, 0.85, 1.0 | 0.75 | Textbook default; no differentiation in eval |

All 9 combinations achieved 10/10 top-1 accuracy on the eval set. Parameters are non-differentiating at this corpus scale; textbook defaults are retained for reproducibility.

---

## Typical Usage

```python
from pathlib import Path
from agentteams.memory_index import build_memory_index, query_index

# Build index from durable sources
sources = sorted(Path("workSummaries").rglob("*.md")) + [
  Path("CHANGELOG.md"),
  Path("README.md"),
]
index = build_memory_index(sources, project_name="agentteams", framework="copilot-vscode")

# Query for relevant documents
results = query_index(index, "behavioral drift detection", k=3)

for r in results:
    print(f"📄 {r['title']} ({r['score']:.3f})")
    print(f"   Path: {r['path']}")
    print(f"   → {r['snippet'][:100]}...")
    for i, passage in enumerate(r['snippets'], 1):
        print(f"   [{i}] {passage[:80]}...")
```

---

## Strategy And Staleness Decision Flows

### Query Strategy Decision

```python
from agentteams.memory_index import query_index

hits = query_index(index, "delivery receipt fingerprint parity", k=5, strategy="lexical")
if not hits:
  # Retry thematic match when lexical confidence is low
  hits = query_index(index, "delivery verification workflow", k=5, strategy="vector")
```

Practical guidance:

- Start with `lexical` for exact-term lookups.
- Retry with `vector` for thematic queries or sparse lexical matches.

### Staleness Handling Decision

```python
from agentteams.memory_index import is_index_stale

if is_index_stale(index, sources):
  # Caller policy: rebuild before serving query results
  index = build_memory_index(sources)

hits = query_index(index, "workflow 11 final check", k=5)
```

### CLI Flow (Operator Path)

```bash
agentteams --description brief.json --refresh-index
agentteams --description brief.json --query-index "security gate overwrite clearance" --query-k 5 --query-strategy lexical
agentteams --description brief.json --query-index "policy guidance for update safety" --query-k 5 --query-strategy vector
```

Use `--refresh-index` after editing durable sources (for example `workSummaries/`, `CHANGELOG.md`, `README.md`, `docs_src/*.md`, `references/*.md`) before query-heavy sessions.

Query CLI synchronization behavior:

- `--query-index` now checks staleness before serving results.
- When stale, it auto-refreshes `references/memory-index.json` and then executes the query.
- `--refresh-index` remains useful for proactive pre-warm workflows and CI/operator scripts.

Incremental update mode (feature-flagged):

- Set `AGENTTEAMS_MEMORY_INDEX_INCREMENTAL_SED=1` to enable a reliability-first incremental updater.
- The updater generates and applies `references/memory-index.incremental-update.sed` for eligible updates.
- Eligibility is intentionally narrow (single changed document, no source set changes, stable term vocabulary).
- On any ineligible shape or sed/validation failure, behavior falls back to canonical full rebuild.

Domain boundary note:

- The `strategy="vector"` option in `memory_index` is local sparse tf·idf vector-space ranking for memory-history retrieval only.
- It is separate from relational retrieval-integrator validation contracts in other module domains.

---

## Integration Notes

- **Durable sources** (I7): Caller is responsible for passing explicit source paths; the module never globs `tmp/` or ignored directories
- **Absence safety** (I5): Empty source list is valid; returns a skeleton index with zero documents
- **Stale detection** (via `is_index_stale()`) — Query CLI auto-refreshes stale indexes; callers may still refresh proactively
- **Fallback protocol** — `@navigator` consults this index first, then opens referenced files for detail, then falls back to filesystem search

---

## Schema Compatibility

The index is forward-compatible within major versions:

- **v1.1 → v1.2 upgrade:** Index built with v1.1 can be queried with v1.2 reader (fallback to static snippet if `paragraphs` missing).
- **v1.2 → v2.0 (hypothetical):** Schema version bump indicates breaking change; consumer must opt-in.

Always check `memory_index_schema_version` before consuming an index artifact.
