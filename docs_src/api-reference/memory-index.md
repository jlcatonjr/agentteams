# `memory_index` — AgentTeamsModule

Build and query a lexical (BM25) search index over durable agent team documentation.

Used by `@navigator` to retrieve relevant work summaries, CHANGELOG entries, and plan artifacts from previous sessions. Pure lexical retrieval (no embeddings) ensures deterministic, testable behavior without external dependencies.

> *Source: `agentteams/memory_index.py`*

---

## Design Principles

- **Lexical, not embeddings** — BM25 scoring; no vector/embedding dependencies
- **Durable sources only** — Caller passes explicit source paths; module never globs ignored directories
- **Robust to absence** — Empty source list → empty but valid index (no error)
- **Graceful degradation** — Per-paragraph passage scoring (I2, I9) with backward-compatible snippet fallback
- **Deterministic** — Same source set always yields same scores; suitable for testing and reproducible workflows

---

## Constants

### `MEMORY_INDEX_SCHEMA_VERSION`

> *Source: `agentteams/memory_index.py`*

Current schema version for memory index artifacts. Used to detect compatibility between build and consumer versions.

**Type:** `str`  
**Current value:** `"1.2"`

**Schema versions:**

| Version | Change |
|---------|--------|
| 1.1 | Legacy: no per-paragraph storage; single `snippet` field only |
| 1.2 | Per-paragraph passage storage; `paragraphs` list added; `query_index()` returns `snippets` list alongside legacy `snippet` alias |

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
- `project_name`, `framework`: Supplied metadata
- `built_at`: ISO-8601 timestamp
- `source_count`: Number of files supplied
- `documents`: List of indexed documents (with title, path, snippet, paragraphs, length, etc.)
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

Check if any source file is newer than the index's `built_at` timestamp.

**Args:**

- `index` (`dict[str, Any]`) — Index dict from `build_memory_index()`.
- `sources` (`Iterable[Path | str]`) — Original source paths (same list used for build, or a subset).

**Returns:** `bool` — `True` if any source mtime > index `built_at` (index is stale); `False` otherwise.

**Safety:** Invalid/missing timestamps are treated as stale (conservative).

---

### `query_index(index, query, *, k=5)`

> *Source: `agentteams/memory_index.py`*

Return the top-*k* documents for *query* by BM25 score.

**Args:**

- `index` (`dict[str, Any]`) — Index dict from `build_memory_index()`.
- `query` (`str`) — Free-text search query (e.g., `"behavioral drift drift detection"`).
- `k` (`int`, keyword-only) — Number of top results to return. Default: `5`.

**Returns:** `list[dict[str, Any]]` — Ranked results (highest BM25 score first). Each result dict contains:
- `doc_id`: Internal document ID
- `path`: Source file path
- `title`: Extracted heading (first `# ` line) or filename
- `score`: BM25 score (rounded to 6 decimal places)
- `snippet`: Best single passage (backward-compat; always present)
- `snippets`: List of up to 3 best-matching passages (new in v1.2; dynamic passage scoring; present even for v1.1 indexes)

**Behavior:**

- Query terms are tokenized and stopwords removed.
- If no query terms match the index, returns empty list.
- Ties are broken deterministically by `(-score, doc_id)` so callers see stable ordering.
- Passage scoring is intra-document only (relative ranking within each document); best paragraphs are ranked independently.
- Backward-compat: v1.1 indexes (no `paragraphs` field) return the stored static `snippet` for all results.

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
sources = [
    Path("workSummaries/daily"),
    Path("CHANGELOG.md"),
    Path("tmp/by-week"),
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

## Integration Notes

- **Durable sources** (I7): Caller is responsible for passing explicit source paths; the module never globs `tmp/` or ignored directories
- **Absence safety** (I5): Empty source list is valid; returns a skeleton index with zero documents
- **Stale detection** (via `is_index_stale()`) — Callers can refresh indexes before querying
- **Fallback protocol** — `@navigator` consults this index first, then opens referenced files for detail, then falls back to filesystem search

---

## Schema Compatibility

The index is forward-compatible within major versions:

- **v1.1 → v1.2 upgrade:** Index built with v1.1 can be queried with v1.2 reader (fallback to static snippet if `paragraphs` missing).
- **v1.2 → v2.0 (hypothetical):** Schema version bump indicates breaking change; consumer must opt-in.

Always check `memory_index_schema_version` before consuming an index artifact.
