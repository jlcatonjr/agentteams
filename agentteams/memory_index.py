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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

MEMORY_INDEX_SCHEMA_VERSION = "1.2"

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
            "source_mtime": _source_mtime(path),
        })
        doc_lengths.append(len(tokens))

    avgdl = (sum(doc_lengths) / len(doc_lengths)) if doc_lengths else 0.0

    return {
        "artifact_type": "memory-index",
        "memory_index_schema_version": MEMORY_INDEX_SCHEMA_VERSION,
        "project_name": project_name,
        "framework": framework,
        "built_at": _utc_now_iso(),
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

    latest_source_ts = 0.0
    for p in sources:
        mtime = _source_mtime(Path(p))
        if mtime > latest_source_ts:
            latest_source_ts = mtime
    return latest_source_ts > built_ts


def query_index(index: dict[str, Any], query: str, *, k: int = 5) -> list[dict[str, Any]]:
    """Return the top-*k* documents for *query* by BM25 score. Deterministic
    tie-break on (-score, doc_id) so callers see a stable ordering.

    Each result includes:
    - ``snippet``: best-matching passage (dynamic; from stored paragraphs when
      available, falling back to the static stored snippet for ``"1.1"`` indexes).
    - ``snippets``: up to ``_SNIPPETS_PER_HIT`` best-matching passages (same
      fallback rule; always a list of at least one string when a hit is returned).
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

    # Build IDF map once for reuse in per-paragraph scoring.
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
    out: list[dict[str, Any]] = []
    for doc_id, score in ranked:
        d = docs[doc_id]
        stored_paragraphs: list[str] = d.get("paragraphs", [])
        if stored_paragraphs and q_terms:
            # Dynamic passage scoring: rank each stored paragraph.
            para_scores = [
                (_score_paragraph(p, q_terms, idf_map), i, p)
                for i, p in enumerate(stored_paragraphs)
            ]
            para_scores.sort(key=lambda x: (-x[0], x[1]))
            best_snippets = [p for _, _, p in para_scores[:_SNIPPETS_PER_HIT]]
            # Ensure snippets are distinct; preserve order.
            seen: set[str] = set()
            distinct_snippets: list[str] = []
            for s in best_snippets:
                if s not in seen:
                    seen.add(s)
                    distinct_snippets.append(s[:240])
            snippets = distinct_snippets or [d["snippet"]]
        else:
            # Backward-compat: 1.1 index has no paragraphs field.
            snippets = [d["snippet"]]
        out.append({
            "doc_id": doc_id,
            "path": d["path"],
            "title": d["title"],
            "score": round(score, 6),
            "snippet": snippets[0],
            "snippets": snippets,
        })
    return out


__all__ = [
    "MEMORY_INDEX_SCHEMA_VERSION",
    "build_memory_index",
    "is_index_stale",
    "query_index",
]
