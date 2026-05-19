"""Retrieval-backed memory index (F8 — additive lexical layer).

A pure, dependency-free **lexical** index (BM25 scoring) over durable text
sources (work-summary docs, CHANGELOG, durable plan/handoff artifacts). The
``@navigator`` consults this index first, then opens the referenced document
for full detail, then falls back to filesystem search — a nested protocol that
is robust to index absence / staleness (this module is additive; the existing
work-summary documents are never modified or replaced).

Design choices (called out for the audit):

- **Lexical, not embeddings.** No new heavy dependencies; deterministic;
  trivially testable. Vector/embedding retrieval is an explicit later tier.
- **Durable sources only.** Callers pass an explicit list of source paths; we
  never glob gitignored ``tmp/`` from inside this module (RSR1).
- **Empty-source ⇒ empty index, no error.** A freshly-generated downstream
  team has no work summaries yet; later ``--update`` runs accrue history.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Iterable

MEMORY_INDEX_SCHEMA_VERSION = "1.0"

# BM25 parameters (textbook defaults).
_K1 = 1.5
_B = 0.75

# Small, generic English stopword set. Kept deliberately short so it is
# obvious and reproducible; do not balloon this into a dependency.
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "are", "was",
    "were", "but", "not", "you", "your", "our", "their", "have", "has", "had",
    "will", "can", "could", "should", "would", "been", "being", "they", "them",
    "its", "his", "her", "she", "him", "who", "what", "when", "where", "why",
    "how", "any", "all", "some", "one", "two", "three", "may", "also", "than",
})

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def _tokenize(text: str) -> list[str]:
    return [
        t.lower()
        for t in _TOKEN_RE.findall(text)
        if t.lower() not in _STOPWORDS
    ]


def _title_for(path: Path, text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return path.name


def _snippet(text: str, max_len: int = 240) -> str:
    s = text.strip().splitlines()
    body = next((ln for ln in s if ln.strip() and not ln.startswith("#")), "")
    return body[:max_len]


def build_memory_index(
    sources: Iterable[Path | str],
    *,
    project_name: str = "",
    framework: str = "",
) -> dict[str, Any]:
    """Build a lexical BM25 index over *sources*. Pure.

    Each source is read once; missing/unreadable sources are silently skipped
    so the index is robust to in-flight file churn. Empty source list ⇒ an
    empty-but-schema-valid index (no error).
    """
    documents: list[dict[str, Any]] = []
    postings: dict[str, list[dict[str, int]]] = {}
    doc_lengths: list[int] = []

    for idx, p in enumerate(sources):
        path = Path(p)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        tokens = _tokenize(text)
        if not tokens:
            continue
        doc_id = len(documents)
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        for term, freq in tf.items():
            postings.setdefault(term, []).append({"doc_id": doc_id, "tf": freq})
        documents.append({
            "doc_id": doc_id,
            "path": str(path),
            "title": _title_for(path, text),
            "length": len(tokens),
            "snippet": _snippet(text),
        })
        doc_lengths.append(len(tokens))

    avgdl = (sum(doc_lengths) / len(doc_lengths)) if doc_lengths else 0.0

    return {
        "artifact_type": "memory-index",
        "memory_index_schema_version": MEMORY_INDEX_SCHEMA_VERSION,
        "project_name": project_name,
        "framework": framework,
        "documents": documents,
        "postings": postings,
        "avgdl": avgdl,
        "N": len(documents),
    }


def query_index(index: dict[str, Any], query: str, *, k: int = 5) -> list[dict[str, Any]]:
    """Return the top-*k* documents for *query* by BM25 score. Deterministic
    tie-break on (-score, doc_id) so callers see a stable ordering.
    """
    n = index.get("N", 0)
    if n == 0:
        return []
    avgdl = index.get("avgdl", 0.0) or 1.0
    docs = index["documents"]
    postings = index["postings"]
    q_terms = [t for t in _tokenize(query) if t in postings]
    if not q_terms:
        return []

    scores: dict[int, float] = {}
    for term in q_terms:
        # IDF (BM25+1 variant guards against negative IDF on small corpora).
        df = len(postings[term])
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        for entry in postings[term]:
            doc_id = entry["doc_id"]
            tf = entry["tf"]
            dl = docs[doc_id]["length"]
            denom = tf + _K1 * (1.0 - _B + _B * dl / avgdl)
            scores[doc_id] = scores.get(doc_id, 0.0) + idf * (tf * (_K1 + 1.0) / denom)

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    out: list[dict[str, Any]] = []
    for doc_id, score in ranked:
        d = docs[doc_id]
        out.append({
            "doc_id": doc_id,
            "path": d["path"],
            "title": d["title"],
            "score": round(score, 6),
            "snippet": d["snippet"],
        })
    return out


__all__ = [
    "MEMORY_INDEX_SCHEMA_VERSION",
    "build_memory_index",
    "query_index",
]
