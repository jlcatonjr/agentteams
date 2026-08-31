"""code_index.py — sparse vector-space retrieval over local scripts and APIs (F-CODEIDX).

The code-retrieval sibling of :mod:`agentteams.memory_index`. Where the memory
index covers durable *prose* (work summaries, CHANGELOG, plans), this module
covers *code*: the repository's own scripts (``source_kind == "local-script"``),
the external API modules those scripts import (``"api-module"``), and the
documentation for those APIs (``"api-doc"``). Every indexed document carries a
``source_kind`` label so a query can be filtered to one kind.

Design (see references/plans/code-api-vector-index.plan.md, audited v3):

- **Local gitignored cache, never committed.** The index lives under
  ``references/code-index/`` as a per-kind set of partition files plus a
  ``manifest.json``. It is a rebuildable cache — not committed, not drift-tracked,
  never staged by a git hook (T2'). This mirrors how ``references/memory-index.json``
  is itself an untracked local artifact.
- **Stdlib-only sparse TF-IDF vector space (T1).** No embeddings, FAISS, chroma.
  ``vector_model_id``/``vector_dim`` are reserved (null) for an optional dense tier.
- **Own copy of the scorers (R2-M3).** The BM25 + sparse-cosine math below is a
  deliberate, behaviour-identical copy of :mod:`agentteams.memory_index`'s scorers
  (Robertson & Zaragoza defaults ``_K1=1.5``, ``_B=0.75``). We do NOT import from
  or refactor the shipped, grid-tuned memory-index module; ``tests/test_code_index.py``
  asserts scoring parity so the copy cannot silently drift.
- **Code-aware tokenizer.** Unlike the prose tokenizer (which drops 2-char tokens
  and does not split identifiers), :func:`_tokenize_code` keeps short identifiers
  (``os``, ``re``, ``id``), keeps dotted import paths whole *and* split, and splits
  snake_case / camelCase so a query for ``batchUpdate`` matches ``batch_update``.
- **Atomic writes (T3').** :func:`atomic_write_json` writes ``*.tmp`` then
  ``os.replace`` so a crash never leaves a half-written manifest that mispoints.
- **Kind-specific staleness (R2-M1).** ``local`` staleness is per-source hash+mtime;
  ``api-*`` staleness is a dependency fingerprint (manifest contents + import set +
  dist→version), because API source lives in site-packages and local mtimes cannot
  witness a dependency upgrade.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

CODE_INDEX_SCHEMA_VERSION = "1.0"
INDEX_FORMAT_VERSION = "json-code-bm25-v1"
INDEX_WRITE_OWNER = "agentteams.code_index"
VECTOR_RUNTIME_MODE = "sparse-tfidf-cosine"
FALLBACK_POLICY = "non-blocking-file-read-then-search"

MANIFEST_ARTIFACT_TYPE = "code-index-manifest"
PARTITION_ARTIFACT_TYPE = "code-index-partition"

SOURCE_KINDS = ("local-script", "api-module", "api-doc")

# BM25 parameters — identical to memory_index (textbook Robertson & Zaragoza).
_K1 = 1.5
_B = 0.75

_MAX_PASSAGES_PER_DOC = 24
_SNIPPETS_PER_HIT = 3

# Per-strategy confidence thresholds — deliberate copy of memory_index's, not an
# import (R2-M3: this module never imports from memory_index; see module docstring).
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
_PASSAGE_MAX_CHARS = 480
_SNIPPET_MAX_CHARS = 240

# Language keywords + noise dropped from the token stream. Kept intentionally
# small and closed (do not balloon into a dependency).
_CODE_STOPWORDS = frozenset({
    # python keywords
    "def", "class", "return", "import", "from", "as", "if", "elif", "else",
    "for", "while", "with", "try", "except", "finally", "raise", "pass",
    "break", "continue", "lambda", "yield", "global", "nonlocal", "assert",
    "del", "in", "is", "and", "or", "not", "none", "true", "false", "self",
    "cls", "async", "await",
    # shell keywords
    "then", "fi", "do", "done", "esac", "elif", "function", "local", "echo",
    "export", "return",
    # generic prose noise that survives identifier tokenizing
    "the", "and", "for", "with", "that", "this", "from", "into", "are",
})

_LANGUAGE_BY_SUFFIX = {".py": "python", ".sh": "shell", ".bash": "shell"}

# Identifiers, keeping leading underscore and dotted paths (os.path.join).
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
# camelCase / PascalCase splitter.
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+|[0-9]+")


# ---------------------------------------------------------------------------
# Tokenization (code-aware)
# ---------------------------------------------------------------------------

def _split_identifier(part: str) -> list[str]:
    """Split one underscore-free identifier fragment into camelCase subtokens."""
    return [m.group(0) for m in _CAMEL_RE.finditer(part)]


def _tokenize_code(text: str) -> list[str]:
    """Tokenize code text into lowercased terms.

    Keeps: whole dotted paths (``os.path``), snake_case parts, camelCase parts,
    and short identifiers (``os``, ``re``, ``id``) that the prose tokenizer drops.
    Drops language keywords and terms shorter than 2 characters.
    """
    out: list[str] = []
    for m in _IDENT_RE.finditer(text):
        raw = m.group(0)
        whole = raw.lower().strip(".")
        parts: list[str] = []
        for dotted in raw.split("."):
            if not dotted:
                continue
            for snake in dotted.split("_"):
                if not snake:
                    continue
                for sub in _split_identifier(snake):
                    s = sub.lower()
                    if len(s) >= 2 and s not in _CODE_STOPWORDS:
                        parts.append(s)
        # Emit the whole dotted/compound form only when it adds information
        # beyond its parts (multi-part identifiers, dotted import paths). For a
        # plain single-part identifier (whole == its only part) emitting both
        # would double its term frequency and skew ranking.
        if (
            len(whole) >= 2
            and whole not in _CODE_STOPWORDS
            and (len(parts) != 1 or parts[0] != whole)
        ):
            out.append(whole)
        out.extend(parts)
    return out


# ---------------------------------------------------------------------------
# Passage extraction (symbol-aware)
# ---------------------------------------------------------------------------

def _signature_for(node: ast.AST) -> str:
    """Render a compact def/class signature via static AST (never executes)."""
    try:
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            return f"class {node.name}({bases})" if bases else f"class {node.name}"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            return f"{kw} {node.name}({ast.unparse(node.args)})"
    except (ValueError, AttributeError, TypeError):  # defensive; ast.unparse edge cases
        return getattr(node, "name", "")
    return getattr(node, "name", "")


def _python_passages(text: str, max_count: int) -> list[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _generic_passages(text, max_count)
    passages: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            head = _signature_for(node)
            doc = ast.get_docstring(node) or ""
            if doc:
                head = f"{head} — {' '.join(doc.split())}"
            passages.append(head[:_PASSAGE_MAX_CHARS])
            if len(passages) >= max_count:
                break
    return passages or _generic_passages(text, max_count)


_SHELL_FUNC_RE = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", re.MULTILINE)


def _shell_passages(text: str, max_count: int) -> list[str]:
    names = [m.group(1) for m in _SHELL_FUNC_RE.finditer(text)]
    passages = [f"function {n}" for n in names[:max_count]]
    return passages or _generic_passages(text, max_count)


def _generic_passages(text: str, max_count: int) -> list[str]:
    """Fallback: non-empty, non-comment lines chunked into short passages."""
    passages: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        lines = [ln for ln in lines if not ln.startswith("#")]
        if not lines:
            continue
        passages.append(" ".join(lines)[:_PASSAGE_MAX_CHARS])
        if len(passages) >= max_count:
            break
    return passages


def _extract_passages(text: str, language: str | None, max_count: int = _MAX_PASSAGES_PER_DOC) -> list[str]:
    if language == "python":
        return _python_passages(text, max_count)
    if language == "shell":
        return _shell_passages(text, max_count)
    return _generic_passages(text, max_count)


def _snippet_from_passages(passages: list[str], text: str) -> str:
    if passages:
        return passages[0][:_SNIPPET_MAX_CHARS]
    for ln in text.splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            return s[:_SNIPPET_MAX_CHARS]
    return ""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _source_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _source_text_hash(path: Path) -> str:
    text = read_text_or_none(path)
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else ""


def read_text_or_none(path: Path) -> str | None:
    """Read UTF-8 text, or return None when unreadable/non-UTF-8 (no swallow).

    Centralising the try/except here keeps callers' skip logic as a plain
    ``if text is None: continue`` (CH-24: no bare ``except: pass``/``continue``).
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _language_for(path: Path) -> str | None:
    return _LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


def _documents_fingerprint(documents: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for doc in sorted(
        documents,
        key=lambda d: (str(d.get("path", "")), str(d.get("symbol") or "")),
    ):
        parts.append(f"{doc.get('path', '')}:{doc.get('symbol') or ''}:{doc.get('source_hash', '')}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write *obj* as pretty JSON atomically (tmp + os.replace) — T3'."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Unit construction
# ---------------------------------------------------------------------------

def local_units(paths: Iterable[Path | str]) -> list[dict[str, Any]]:
    """Build ``local-script`` index units from on-disk source files.

    Unreadable / non-UTF-8 files are silently skipped (robust to churn).
    """
    units: list[dict[str, Any]] = []
    for raw in paths:
        p = Path(raw)
        text = read_text_or_none(p)
        if text is None:
            continue
        units.append({
            "path": str(p),
            "text": text,
            "language": _language_for(p),
            "source_kind": "local-script",
            "symbol": None,
            "signature": None,
            "provenance": None,
        })
    return units


# ---------------------------------------------------------------------------
# Partition build
# ---------------------------------------------------------------------------

def build_code_partition(
    units: Iterable[dict[str, Any]],
    *,
    source_kind: str,
    project_name: str = "",
    framework: str = "",
    dependency_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Build one schema-valid partition index over *units*. Pure.

    Each unit is a dict with keys ``path``, ``text`` and optional ``language``,
    ``symbol``, ``signature``, ``provenance``. Units are sorted deterministically
    by ``(source_kind, path, symbol)`` before ``doc_id`` assignment so a rebuild
    of unchanged inputs yields identical structure (T6'). An empty unit list
    yields an empty-but-schema-valid partition (no error).
    """
    normalized = [u for u in units if isinstance(u, dict)]
    normalized.sort(key=lambda u: (
        str(u.get("source_kind", source_kind)),
        str(u.get("path", "")),
        str(u.get("symbol") or ""),
    ))

    documents: list[dict[str, Any]] = []
    postings: dict[str, list[dict[str, int]]] = {}
    doc_lengths: list[int] = []

    for u in normalized:
        text = u.get("text", "") or ""
        tokens = _tokenize_code(text)
        if not tokens and not u.get("symbol"):
            continue
        doc_id = len(documents)
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        for term, freq in tf.items():
            postings.setdefault(term, []).append({"doc_id": doc_id, "tf": freq})
        language = u.get("language")
        passages = _extract_passages(text, language)
        path_str = str(u.get("path", ""))
        title = u.get("symbol") or (Path(path_str).name if path_str else "") or path_str
        documents.append({
            "doc_id": doc_id,
            "path": path_str,
            "source_kind": u.get("source_kind", source_kind),
            "language": language,
            "symbol": u.get("symbol"),
            "signature": u.get("signature"),
            "provenance": u.get("provenance"),
            "title": title,
            "length": len(tokens),
            "snippet": _snippet_from_passages(passages, text),
            "passages": passages,
            "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source_mtime": _source_mtime(Path(path_str)) if path_str else 0.0,
        })
        doc_lengths.append(len(tokens))

    avgdl = (sum(doc_lengths) / len(doc_lengths)) if doc_lengths else 0.0

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
        f"{source_fingerprint}|{built_at}|{len(normalized)}".encode("utf-8")
    ).hexdigest()

    return {
        "artifact_type": PARTITION_ARTIFACT_TYPE,
        "code_index_schema_version": CODE_INDEX_SCHEMA_VERSION,
        "index_format_version": INDEX_FORMAT_VERSION,
        "index_build_id": index_build_id,
        "index_write_owner": INDEX_WRITE_OWNER,
        "vector_runtime_mode": VECTOR_RUNTIME_MODE,
        "vector_model_id": None,
        "vector_dim": None,
        "fallback_policy": FALLBACK_POLICY,
        "source_fingerprint": source_fingerprint,
        "dependency_fingerprint": dependency_fingerprint,
        "source_kind": source_kind,
        "project_name": project_name,
        "framework": framework,
        "built_at": built_at,
        "source_count": len(normalized),
        "documents": documents,
        "postings": postings,
        "avgdl": avgdl,
        "N": len(documents),
    }


def partition_content_fingerprint(partition: dict[str, Any]) -> str:
    """Content identity excluding volatile fields (built_at, build id, mtimes).

    Used to skip rewriting a partition whose *content* is unchanged, so an
    on-disk file (and its built_at) stays stable across no-op rebuilds.
    """
    docs = partition.get("documents", [])
    doc_ids = [
        (d.get("path"), d.get("symbol"), d.get("source_kind"), d.get("source_hash"))
        for d in docs if isinstance(d, dict)
    ]
    payload = json.dumps(
        {
            "source_kind": partition.get("source_kind"),
            "dependency_fingerprint": partition.get("dependency_fingerprint"),
            "source_fingerprint": partition.get("source_fingerprint"),
            "N": partition.get("N"),
            "avgdl": round(float(partition.get("avgdl", 0.0)), 6),
            "docs": doc_ids,
            "postings": partition.get("postings", {}),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Staleness (kind-specific — R2-M1)
# ---------------------------------------------------------------------------

def dependency_fingerprint(
    manifest_texts: Iterable[str],
    import_names: Iterable[str],
    dist_versions: Iterable[tuple[str, str]],
) -> str:
    """Fingerprint the API-facing dependency surface.

    An ``api-*`` partition is stale when this changes: the contents of the
    dependency manifests (pyproject/requirements/lockfiles), the set of external
    import names used by local scripts, or the resolved dist→version map. Local
    script mtimes cannot witness a dependency upgrade, so ``api-*`` staleness
    must key on this rather than on source mtimes (plan R2-M1).
    """
    h = hashlib.sha256()
    for text in manifest_texts:
        h.update(b"M\x00")
        h.update(text.encode("utf-8"))
    for name in sorted(set(import_names)):
        h.update(b"I\x00")
        h.update(name.encode("utf-8"))
    for dist, ver in sorted(set(dist_versions)):
        h.update(b"D\x00")
        h.update(f"{dist}=={ver}".encode("utf-8"))
    return h.hexdigest()


def _built_at_invalid(partition: dict[str, Any]) -> bool:
    built_at = partition.get("built_at")
    if not isinstance(built_at, str) or not built_at:
        return True
    try:
        datetime.fromisoformat(built_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return False


def is_partition_stale(
    partition: dict[str, Any],
    sources: Iterable[Path | str] | None = None,
    *,
    dependency_fingerprint: str | None = None,
) -> bool:
    """Return True when *partition* no longer reflects its sources.

    ``api-module``/``api-doc`` partitions compare the stored dependency
    fingerprint against *dependency_fingerprint*. ``local-script`` partitions
    use a per-document hash gate plus an mtime-vs-built_at gate (mirrors
    :func:`agentteams.memory_index.is_index_stale`). Missing/invalid metadata is
    treated as stale for safety.
    """
    if _built_at_invalid(partition):
        return True
    source_kind = partition.get("source_kind")
    if source_kind in ("api-module", "api-doc"):
        return partition.get("dependency_fingerprint") != dependency_fingerprint

    built_dt = datetime.fromisoformat(str(partition["built_at"]).replace("Z", "+00:00"))
    built_ts = built_dt.timestamp()
    for doc in partition.get("documents", []):
        if not isinstance(doc, dict):
            continue
        path_str = doc.get("path")
        expected_hash = doc.get("source_hash")
        if isinstance(path_str, str) and isinstance(expected_hash, str) and expected_hash:
            actual_hash = _source_text_hash(Path(path_str))
            if not actual_hash or actual_hash != expected_hash:
                return True
    latest = 0.0
    for p in (sources or []):
        mtime = _source_mtime(Path(p))
        if mtime > latest:
            latest = mtime
    return latest > built_ts


# ---------------------------------------------------------------------------
# Query — own copy of the memory_index scorers (R2-M3), code-tokenized
# ---------------------------------------------------------------------------

def _score_passage(passage: str, q_terms: list[str], idf_map: dict[str, float]) -> float:
    tokens = _tokenize_code(passage)
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
            score += idf * (tf * (_K1 + 1.0)) / (tf + _K1 * (1.0 - _B + _B * dl / max(dl, 1)))
    return score


def _ranked_hits(
    partition: dict[str, Any],
    ranked: list[tuple[int, float]],
    *,
    q_terms: list[str],
    idf_map: dict[str, float],
    strategy: str,
) -> list[dict[str, Any]]:
    docs = partition["documents"]
    out: list[dict[str, Any]] = []
    for doc_id, score in ranked:
        d = docs[doc_id]
        stored_passages: list[str] = d.get("passages", [])
        if stored_passages and q_terms:
            scored = [
                (_score_passage(p, q_terms, idf_map), i, p)
                for i, p in enumerate(stored_passages)
            ]
            scored.sort(key=lambda x: (-x[0], x[1]))
            seen: set[str] = set()
            snippets: list[str] = []
            for _, _, p in scored[:_SNIPPETS_PER_HIT]:
                if p not in seen:
                    seen.add(p)
                    snippets.append(p[:_SNIPPET_MAX_CHARS])
            snippets = snippets or [d["snippet"]]
        else:
            snippets = [d["snippet"]]
        out.append({
            "doc_id": doc_id,
            "path": d["path"],
            "title": d["title"],
            "source_kind": d.get("source_kind"),
            "language": d.get("language"),
            "symbol": d.get("symbol"),
            "signature": d.get("signature"),
            "provenance": d.get("provenance"),
            "score": round(score, 6),
            "confidence": _confidence_for(strategy, score),
            "snippet": snippets[0],
            "snippets": snippets,
        })
    return out


def _query_partition_lexical(partition: dict[str, Any], query: str, *, k: int) -> list[dict[str, Any]]:
    n = partition.get("N", 0)
    if n == 0:
        return []
    avgdl = partition.get("avgdl", 0.0) or 1.0
    docs = partition["documents"]
    postings = partition["postings"]
    q_terms = [t for t in _tokenize_code(query) if t in postings]
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
    return _ranked_hits(partition, ranked, q_terms=q_terms, idf_map=idf_map, strategy="lexical")


def _query_partition_vector(partition: dict[str, Any], query: str, *, k: int) -> list[dict[str, Any]]:
    n = partition.get("N", 0)
    if n == 0:
        return []
    docs = partition["documents"]
    postings = partition["postings"]
    q_tokens = _tokenize_code(query)
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
    scores: dict[int, float] = {}
    for doc_id, d in dot.items():
        stored = docs[doc_id].get("vector_norm_sq")
        if stored is None:
            stored = 0.0
        denom = math.sqrt(stored) * q_norm
        if denom > 0.0:
            scores[doc_id] = d / denom
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return _ranked_hits(partition, ranked, q_terms=list(q_weights.keys()), idf_map=idf_map, strategy="vector")


def query_partition(
    partition: dict[str, Any],
    query: str,
    *,
    k: int = 5,
    strategy: str = "lexical",
) -> list[dict[str, Any]]:
    """Return the top-*k* hits for *query* within a single partition.

    Each hit also carries a ``confidence`` field ("reliable" / "candidate" / "weak"),
    computed from the same per-strategy thresholds callers previously had to apply by hand.
    """
    if strategy == "lexical":
        return _query_partition_lexical(partition, query, k=k)
    if strategy == "vector":
        return _query_partition_vector(partition, query, k=k)
    raise ValueError(f"Unknown query strategy: {strategy!r}")


_KIND_FILTER = {
    "local": {"local-script"},
    "api": {"api-module"},
    "doc": {"api-doc"},
    "all": set(SOURCE_KINDS),
}


def query_partitions(
    partitions: dict[str, dict[str, Any]],
    query: str,
    *,
    k: int = 5,
    strategy: str = "lexical",
    kind: str = "all",
) -> list[dict[str, Any]]:
    """Query across partitions, filtered by *kind*, merged by score.

    *kind* is one of ``local``/``api``/``doc``/``all``. Scores are computed
    independently per partition (each has its own idf/avgdl), so cross-partition
    ordering is approximate — acceptable for a retrieval shortlist.
    """
    allowed = _KIND_FILTER.get(kind, _KIND_FILTER["all"])
    hits: list[dict[str, Any]] = []
    for part in partitions.values():
        if not isinstance(part, dict):
            continue
        if part.get("source_kind") not in allowed:
            continue
        hits.extend(query_partition(part, query, k=k, strategy=strategy))
    hits.sort(key=lambda h: (-h["score"], str(h.get("path", "")), str(h.get("symbol") or "")))
    return hits[:k]


# ---------------------------------------------------------------------------
# Cache manifest
# ---------------------------------------------------------------------------

def build_manifest(
    partition_meta: dict[str, dict[str, Any]],
    *,
    project_name: str = "",
    framework: str = "",
    query_entrypoints: list[str] | None = None,
    maintenance_entrypoints: list[str] | None = None,
    trigger_sources: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the code-index cache manifest from per-partition metadata.

    *partition_meta* maps partition name → the built partition dict (or a light
    meta dict). Trigger metadata lives ONLY here (never in the project/team
    manifest — plan C2-1/C-4).
    """
    partitions: dict[str, Any] = {}
    for name, part in partition_meta.items():
        partitions[name] = {
            "file": f"{name}.json",
            "source_kind": part.get("source_kind"),
            "N": int(part.get("N", 0)),
            "source_count": int(part.get("source_count", 0)),
            "built_at": part.get("built_at"),
            "source_fingerprint": part.get("source_fingerprint"),
            "dependency_fingerprint": part.get("dependency_fingerprint"),
        }
    return {
        "artifact_type": MANIFEST_ARTIFACT_TYPE,
        "code_index_schema_version": CODE_INDEX_SCHEMA_VERSION,
        "index_format_version": INDEX_FORMAT_VERSION,
        "index_write_owner": INDEX_WRITE_OWNER,
        "vector_runtime_mode": VECTOR_RUNTIME_MODE,
        "vector_model_id": None,
        "vector_dim": None,
        "fallback_policy": FALLBACK_POLICY,
        "project_name": project_name,
        "framework": framework,
        "built_at": _utc_now_iso(),
        "partitions": partitions,
        "query_entrypoints": query_entrypoints or ["agentteams --query-code", "/code-recall"],
        "maintenance_entrypoints": maintenance_entrypoints
        or ["agentteams --refresh-code-index", "agentteams --update"],
        "trigger_sources": trigger_sources or ["cli", "script"],
    }


__all__ = [
    "CODE_INDEX_SCHEMA_VERSION",
    "INDEX_FORMAT_VERSION",
    "INDEX_WRITE_OWNER",
    "VECTOR_RUNTIME_MODE",
    "FALLBACK_POLICY",
    "MANIFEST_ARTIFACT_TYPE",
    "PARTITION_ARTIFACT_TYPE",
    "SOURCE_KINDS",
    "local_units",
    "build_code_partition",
    "partition_content_fingerprint",
    "atomic_write_json",
    "dependency_fingerprint",
    "is_partition_stale",
    "query_partition",
    "query_partitions",
    "build_manifest",
]
