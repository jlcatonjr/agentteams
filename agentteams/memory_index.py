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
- **Per-paragraph passage scoring (I2/I9).** Each document stores its
  substantive paragraphs at build time (up to ``_MAX_PARAGRAPHS_PER_DOC``).
  At query time, ``query_index()`` scores each stored paragraph against the
  query terms and returns up to ``_SNIPPETS_PER_HIT`` best-matching paragraphs
  as ``snippets: list[str]`` alongside the legacy ``snippet: str`` alias
  (``snippets[0]``). Indexes built with schema ``"1.1"`` (no ``paragraphs``
  field) fall back gracefully to the stored ``snippet``.

BM25 parameter note (I4 — grid-searched on AgentTeams N≈105 corpus, 2026-05-19):
  Grid K1 ∈ {1.2, 1.5, 2.0} × B ∈ {0.75, 0.85, 1.0} all achieved 10/10 top-1
  accuracy on the eval set in tests/test_memory_index_relevance.py — parameters
  are non-differentiating at this corpus scale.  Textbook defaults retained.
"""

from __future__ import annotations

import math
import re
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

MEMORY_INDEX_SCHEMA_VERSION = "1.3"
INDEX_FORMAT_VERSION = "json-bm25-postings-v2"
INDEX_WRITE_OWNER = "agentteams.build_team"
VECTOR_RUNTIME_MODE = "sparse-tfidf-cosine"
FALLBACK_POLICY = "non-blocking-file-read-then-search"

# BM25 parameters.  Grid-searched K1 ∈ {1.2, 1.5, 2.0} × B ∈ {0.75, 0.85, 1.0}
# on the AgentTeams N≈105 corpus (2026-05-19) using the eval set in
# tests/test_memory_index_relevance.py.  All 9 combinations achieved 10/10
# top-1 accuracy — parameters are non-differentiating at this corpus scale.
# Textbook defaults (Robertson & Zaragoza, 2009) are retained.
_K1 = 1.5
_B = 0.75

# Per-paragraph passage storage (I2) and multi-snippet output (I9).
_MAX_PARAGRAPHS_PER_DOC = 20
_SNIPPETS_PER_HIT = 3

# Per-strategy confidence thresholds (the two scales are not comparable —
# lexical is an unbounded BM25 score, vector is a cosine similarity in
# [0, 1]). These are the same thresholds templates under agentteams/templates/
# used to restate as prose in a memory_index_consultation fence; query_index()
# now computes the categorical label directly so that prose can cite the field
# instead of re-deriving it.
_CONFIDENCE_THRESHOLDS: dict[str, dict[str, float]] = {
    "lexical": {"reliable": 3.0, "candidate": 1.0},
    "vector": {"reliable": 0.30, "candidate": 0.20},
}


def _confidence_for(strategy: str, score: float) -> str:
    """Categorize a raw hit score into "reliable" / "candidate" / "weak"."""
    bounds = _CONFIDENCE_THRESHOLDS[strategy]
    if score >= bounds["reliable"]:
        return "reliable"
    if score >= bounds["candidate"]:
        return "candidate"
    return "weak"

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


def _is_substantive_paragraph(paragraph: str) -> bool:
    p = paragraph.strip()
    if not p:
        return False
    lower = p.lower()
    if lower.startswith("<!--"):
        return False
    if lower.startswith("all notable changes to this project will be documented"):
        return False
    if "---|" in p or p.startswith("|"):
        return False
    if lower.startswith("[!["):
        return False
    # Standalone heading lines (no body content after) are not snippets.
    # Multi-line paragraphs that start with a heading but have body content
    # below it (e.g. ### section\n- bullet1\n- bullet2) are substantive.
    lines = [l.strip() for l in p.splitlines() if l.strip()]
    if all(l.startswith("#") for l in lines):
        return False
    words = re.findall(r"[A-Za-z0-9_-]+", p)
    return len(words) >= 8


def _extract_paragraphs(text: str, max_count: int = _MAX_PARAGRAPHS_PER_DOC) -> list[str]:
    """Return up to *max_count* substantive paragraphs from *text*.

    Each paragraph is normalised to a single line (internal newlines → space)
    and truncated to 480 characters so the JSON stays compact while giving
    the query-time scorer enough context to rank meaningfully.  Leading heading
    lines are stripped so snippets begin with actual body content.
    """
    raw = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    result: list[str] = []
    for p in raw:
        if _is_substantive_paragraph(p):
            # Strip leading heading line(s) so the snippet starts with content.
            content_lines = [l for l in p.splitlines() if not l.strip().startswith("#")]
            body = " ".join(l.strip() for l in content_lines if l.strip())
            if not body:
                body = p.replace("\n", " ")
            result.append(body[:480])
            if len(result) >= max_count:
                break
    return result


def _snippet_from_paragraphs(paragraphs: list[str], max_len: int = 240) -> str:
    """Best single-paragraph fallback snippet (first substantive entry)."""
    return paragraphs[0][:max_len] if paragraphs else ""


def _snippet(text: str, max_len: int = 240) -> str:
    """Legacy single-snippet extractor; used only for backward-compat fallback."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for para in paragraphs:
        if _is_substantive_paragraph(para):
            content_lines = [l for l in para.splitlines() if not l.strip().startswith("#")]
            body = " ".join(l.strip() for l in content_lines if l.strip())
            return (body or para.replace("\n", " "))[:max_len]
    # Safe fallback: first non-empty non-heading line.
    lines = text.strip().splitlines()
    body = next((ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")), "")
    return body[:max_len]


def _score_paragraph(paragraph: str, q_terms: list[str], idf_map: dict[str, float]) -> float:
    """Score a single paragraph against query terms using simple tf·idf overlap.

    This is a lightweight passage scorer — not full BM25 (we don't have per-
    paragraph document-length stats at query time).  The goal is relative
    ranking of paragraphs within a single document so the most query-relevant
    passage wins, not cross-document comparison.
    """
    tokens = _tokenize(paragraph)
    if not tokens:
        return 0.0
    tf_local: dict[str, int] = {}
    for t in tokens:
        tf_local[t] = tf_local.get(t, 0) + 1
    dl = len(tokens)
    score = 0.0
    for term in q_terms:
        if term in tf_local:
            idf = idf_map.get(term, 0.0)
            tf = tf_local[term]
            # Simplified BM25-like passage score (no avgdl — intra-doc ranking only).
            score += idf * (tf * (_K1 + 1.0)) / (tf + _K1 * (1.0 - _B + _B * dl / max(dl, 1)))
    return score


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _source_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _source_text_hash(path: Path) -> str:
    """Return SHA-256 of source text, or empty string when unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _documents_fingerprint(documents: list[dict[str, Any]]) -> str:
    """Stable fingerprint over indexed document path/hash pairs."""
    parts: list[str] = []
    for doc in sorted(documents, key=lambda d: str(d.get("path", ""))):
        parts.append(f"{doc.get('path', '')}:{doc.get('source_hash', '')}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


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
    source_paths = [Path(p) for p in sources]
    documents: list[dict[str, Any]] = []
    postings: dict[str, list[dict[str, int]]] = {}
    doc_lengths: list[int] = []

    for path in source_paths:
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
        paragraphs = _extract_paragraphs(text)
        documents.append({
            "doc_id": doc_id,
            "path": str(path),
            "title": _title_for(path, text),
            "length": len(tokens),
            "snippet": _snippet_from_paragraphs(paragraphs) or _snippet(text),
            "paragraphs": paragraphs,
            "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source_mtime": _source_mtime(path),
        })
        doc_lengths.append(len(tokens))

    avgdl = (sum(doc_lengths) / len(doc_lengths)) if doc_lengths else 0.0

    # Precompute each document's full IDF-weighted TF-IDF vector norm-squared
    # for vector-strategy cosine. Using the full norm (not just the
    # query-intersection norm) is required for the cosine denominator to be
    # mathematically correct; otherwise short queries with one matching term
    # produce a trivial cosine of 1.0.
    n_docs = len(documents)
    doc_norm_sq = [0.0] * n_docs
    for term, entries in postings.items():
        df = len(entries)
        idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
        for entry in entries:
            w = entry["tf"] * idf
            doc_norm_sq[entry["doc_id"]] += w * w
    for doc_id, ns in enumerate(doc_norm_sq):
        documents[doc_id]["vector_norm_sq"] = ns

    built_at = _utc_now_iso()
    source_fingerprint = _documents_fingerprint(documents)
    index_build_id = hashlib.sha256(
        f"{source_fingerprint}|{built_at}|{len(source_paths)}".encode("utf-8")
    ).hexdigest()

    return {
        "artifact_type": "memory-index",
        "memory_index_schema_version": MEMORY_INDEX_SCHEMA_VERSION,
        "index_format_version": INDEX_FORMAT_VERSION,
        "index_build_id": index_build_id,
        "index_write_owner": INDEX_WRITE_OWNER,
        "vector_runtime_mode": VECTOR_RUNTIME_MODE,
        "vector_model_id": None,
        "vector_dim": None,
        "fallback_policy": FALLBACK_POLICY,
        "source_fingerprint": source_fingerprint,
        "project_name": project_name,
        "framework": framework,
        "built_at": built_at,
        "source_count": len(source_paths),
        "documents": documents,
        "postings": postings,
        "avgdl": avgdl,
        "N": len(documents),
    }


def is_index_stale(index: dict[str, Any], sources: Iterable[Path | str]) -> bool:
    """Return True when any source file is newer than the index build time.

    Invalid/missing timestamp metadata is treated as stale for safety.
    """
    built_at = index.get("built_at")
    if not isinstance(built_at, str) or not built_at:
        return True
    try:
        built_dt = datetime.fromisoformat(built_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    built_ts = built_dt.timestamp()

    # Hash-aware freshness when available. We still keep the legacy mtime gate
    # for conservative stale detection.
    for doc in index.get("documents", []):
        if not isinstance(doc, dict):
            continue
        path_str = doc.get("path")
        expected_hash = doc.get("source_hash")
        if isinstance(path_str, str) and isinstance(expected_hash, str) and expected_hash:
            actual_hash = _source_text_hash(Path(path_str))
            if not actual_hash or actual_hash != expected_hash:
                return True

    latest_source_ts = 0.0
    for p in sources:
        mtime = _source_mtime(Path(p))
        if mtime > latest_source_ts:
            latest_source_ts = mtime
    return latest_source_ts > built_ts


def _ranked_hits(
    index: dict[str, Any],
    ranked: list[tuple[int, float]],
    *,
    q_terms: list[str],
    idf_map: dict[str, float],
    strategy: str,
) -> list[dict[str, Any]]:
    """Format ranked doc_ids into API hit records with dynamic snippets."""
    docs = index["documents"]
    out: list[dict[str, Any]] = []
    for doc_id, score in ranked:
        d = docs[doc_id]
        stored_paragraphs: list[str] = d.get("paragraphs", [])
        if stored_paragraphs and q_terms:
            para_scores = [
                (_score_paragraph(p, q_terms, idf_map), i, p)
                for i, p in enumerate(stored_paragraphs)
            ]
            para_scores.sort(key=lambda x: (-x[0], x[1]))
            best_snippets = [p for _, _, p in para_scores[:_SNIPPETS_PER_HIT]]
            seen: set[str] = set()
            distinct_snippets: list[str] = []
            for s in best_snippets:
                if s not in seen:
                    seen.add(s)
                    distinct_snippets.append(s[:240])
            snippets = distinct_snippets or [d["snippet"]]
        else:
            snippets = [d["snippet"]]
        out.append({
            "doc_id": doc_id,
            "path": d["path"],
            "title": d["title"],
            "score": round(score, 6),
            "confidence": _confidence_for(strategy, score),
            "snippet": snippets[0],
            "snippets": snippets,
        })
    return out


def _query_index_lexical(index: dict[str, Any], query: str, *, k: int = 5) -> list[dict[str, Any]]:
    """Return top-*k* hits using BM25 lexical ranking."""
    n = index.get("N", 0)
    if n == 0:
        return []
    avgdl = index.get("avgdl", 0.0) or 1.0
    docs = index["documents"]
    postings = index["postings"]
    q_terms = [t for t in _tokenize(query) if t in postings]
    if not q_terms:
        return []

    idf_map: dict[str, float] = {}
    scores: dict[int, float] = {}
    for term in q_terms:
        df = len(postings[term])
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        idf_map[term] = idf
        for entry in postings[term]:
            doc_id = entry["doc_id"]
            tf = entry["tf"]
            dl = docs[doc_id]["length"]
            denom = tf + _K1 * (1.0 - _B + _B * dl / avgdl)
            scores[doc_id] = scores.get(doc_id, 0.0) + idf * (tf * (_K1 + 1.0) / denom)

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return _ranked_hits(index, ranked, q_terms=q_terms, idf_map=idf_map, strategy="lexical")


def _query_index_vector(index: dict[str, Any], query: str, *, k: int = 5) -> list[dict[str, Any]]:
    """Return top-*k* hits using a deterministic sparse vector-space scorer.

    This provides an optional vector-space retrieval mode without adding heavy
    dependencies. Lexical BM25 remains the default query strategy.
    """
    n = index.get("N", 0)
    if n == 0:
        return []
    docs = index["documents"]
    postings = index["postings"]
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []

    q_tf: dict[str, int] = {}
    for t in q_tokens:
        if t in postings:
            q_tf[t] = q_tf.get(t, 0) + 1
    if not q_tf:
        return []

    idf_map: dict[str, float] = {}
    q_weights: dict[str, float] = {}
    q_norm_sq = 0.0
    for term, tfq in q_tf.items():
        df = len(postings[term])
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        idf_map[term] = idf
        wq = tfq * idf
        q_weights[term] = wq
        q_norm_sq += wq * wq
    if q_norm_sq == 0.0:
        return []
    q_norm = math.sqrt(q_norm_sq)

    dot: dict[int, float] = {}
    for term, wq in q_weights.items():
        for entry in postings[term]:
            doc_id = entry["doc_id"]
            wd = entry["tf"] * idf_map[term]
            dot[doc_id] = dot.get(doc_id, 0.0) + (wd * wq)

    # Use each document's full TF-IDF vector norm — not the partial norm
    # restricted to query-intersection terms — so cosine is a true similarity
    # over the shared term space, not the artificially-parallel subspace.
    # Legacy indices (pre v2) lack the stored norm; reconstruct on demand.
    doc_norm_sq_full: dict[int, float] | None = None
    def _legacy_doc_norm_sq() -> dict[int, float]:
        ns: dict[int, float] = {}
        for term, entries in postings.items():
            df = len(entries)
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for entry in entries:
                w = entry["tf"] * idf
                doc_id = entry["doc_id"]
                ns[doc_id] = ns.get(doc_id, 0.0) + w * w
        return ns

    scores: dict[int, float] = {}
    for doc_id, d in dot.items():
        stored = docs[doc_id].get("vector_norm_sq")
        if stored is None:
            if doc_norm_sq_full is None:
                doc_norm_sq_full = _legacy_doc_norm_sq()
            stored = doc_norm_sq_full.get(doc_id, 0.0)
        denom = math.sqrt(stored) * q_norm
        if denom > 0.0:
            scores[doc_id] = d / denom

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return _ranked_hits(index, ranked, q_terms=list(q_weights.keys()), idf_map=idf_map, strategy="vector")


def query_index(
    index: dict[str, Any],
    query: str,
    *,
    k: int = 5,
    strategy: str = "lexical",
) -> list[dict[str, Any]]:
    """Return the top-*k* documents for *query*.

    Strategies:
    - ``"lexical"``: BM25 lexical scoring (default).
    - ``"vector"``: sparse vector-space cosine scoring (deterministic, stdlib-only).

    Each hit also carries a ``confidence`` field ("reliable" / "candidate" / "weak"),
    computed from the same per-strategy thresholds callers previously had to apply by hand.
    """
    if strategy == "lexical":
        return _query_index_lexical(index, query, k=k)
    if strategy == "vector":
        return _query_index_vector(index, query, k=k)
    raise ValueError(f"Unknown query strategy: {strategy!r}")


__all__ = [
    "MEMORY_INDEX_SCHEMA_VERSION",
    "INDEX_FORMAT_VERSION",
    "INDEX_WRITE_OWNER",
    "VECTOR_RUNTIME_MODE",
    "FALLBACK_POLICY",
    "build_memory_index",
    "is_index_stale",
    "query_index",
]
