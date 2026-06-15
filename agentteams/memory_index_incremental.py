"""Incremental sed-based updates for references/memory-index.json.

This module provides a reliability-first incremental path with narrow eligibility:
- exactly one changed source document
- no added/removed documents
- unchanged term vocabulary for the changed document

Any mismatch, sed failure, parse/schema mismatch, or ineligible shape returns a
non-applied result so callers can fall back to canonical full rebuild.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from agentteams import memory_index as mi


@dataclass(frozen=True)
class IncrementalUpdateResult:
    applied: bool
    reason: str


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _source_hash(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _old_tf_for_doc(index: dict[str, Any], doc_id: int) -> dict[str, int]:
    out: dict[str, int] = {}
    postings = index.get("postings", {})
    if not isinstance(postings, dict):
        return out
    for term, entries in postings.items():
        if not isinstance(term, str) or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("doc_id") == doc_id and isinstance(entry.get("tf"), int):
                out[term] = entry["tf"]
    return out


def _new_doc_and_tf(path: Path, doc_id: int) -> tuple[dict[str, Any], dict[str, int]] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    tokens = mi._tokenize(text)
    if not tokens:
        return None

    tf: dict[str, int] = {}
    for token in tokens:
        tf[token] = tf.get(token, 0) + 1

    paragraphs = mi._extract_paragraphs(text)
    doc = {
        "doc_id": doc_id,
        "path": str(path),
        "title": mi._title_for(path, text),
        "length": len(tokens),
        "snippet": mi._snippet_from_paragraphs(paragraphs) or mi._snippet(text),
        "paragraphs": paragraphs,
        "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_mtime": _source_mtime(path),
    }
    return doc, tf


def _format_doc_inner_lines(doc: dict[str, Any]) -> list[str]:
    lines = []
    lines.append(f'      "doc_id": {doc["doc_id"]},')
    lines.append(f'      "path": {json.dumps(doc["path"])},')
    lines.append(f'      "title": {json.dumps(doc["title"])},')
    lines.append(f'      "length": {doc["length"]},')
    lines.append(f'      "snippet": {json.dumps(doc["snippet"])},')
    lines.append('      "paragraphs": [')
    paragraphs: list[str] = doc.get("paragraphs", [])
    for idx, paragraph in enumerate(paragraphs):
        suffix = "," if idx < len(paragraphs) - 1 else ""
        lines.append(f"        {json.dumps(paragraph)}{suffix}")
    lines.append("      ],")
    lines.append(f'      "source_hash": {json.dumps(doc["source_hash"])},')
    lines.append(f'      "source_mtime": {doc["source_mtime"]}')
    return lines


def _format_term_block(term: str, entries: list[dict[str, int]], has_trailing_comma: bool) -> list[str]:
    lines = [f'    {json.dumps(term)}: [']
    for idx, entry in enumerate(entries):
        comma = "," if idx < len(entries) - 1 else ""
        lines.append("      {")
        lines.append(f'        "doc_id": {entry["doc_id"]},')
        lines.append(f'        "tf": {entry["tf"]}')
        lines.append(f"      }}{comma}")
    closing = "    ]," if has_trailing_comma else "    ]"
    lines.append(closing)
    return lines


def _find_line(lines: list[str], prefix: str) -> int | None:
    for idx, line in enumerate(lines):
        if line.startswith(prefix):
            return idx + 1
    return None


def _find_doc_inner_range(lines: list[str], doc_id: int) -> tuple[int, int] | None:
    start = None
    end = None
    needle = f'      "doc_id": {doc_id},'
    for idx, line in enumerate(lines):
        if line == needle:
            start = idx + 1
            break
    if start is None:
        return None
    last_field_idx = None
    for idx in range(start - 1, len(lines)):
        line = lines[idx]
        # The last field of a document varies across index_format_versions:
        # v1 ends at source_mtime, v2 may add vector_norm_sq after it. Track
        # whichever appears last within this doc's block (stop at the closing
        # brace).
        if line.startswith('      "source_mtime": ') or line.startswith('      "vector_norm_sq": '):
            last_field_idx = idx
        elif line.startswith("    }"):
            break
    if last_field_idx is None:
        return None
    end = last_field_idx + 1
    if end < start:
        return None
    return start, end


def _find_term_range(lines: list[str], term: str) -> tuple[int, int, bool] | None:
    start = None
    end = None
    has_comma = True
    term_line = f"    {json.dumps(term)}: ["
    for idx, line in enumerate(lines):
        if line == term_line:
            start = idx + 1
            break
    if start is None:
        return None
    for idx in range(start - 1, len(lines)):
        if lines[idx] in ("    ],", "    ]"):
            end = idx + 1
            has_comma = lines[idx].endswith(",")
            break
    if end is None or end < start:
        return None
    return start, end, has_comma


def _sed_escape_line(line: str) -> str:
    # Escape backslashes and ampersands for safety in sed replacement text.
    return line.replace("\\", "\\\\").replace("&", "\\&")


def _write_sed_script(script_path: Path, commands: list[str]) -> None:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("\n".join(commands) + "\n", encoding="utf-8")


def _replace_with_sed(script_path: Path, index_path: Path) -> tuple[bool, str]:
    try:
        subprocess.run(
            ["sed", "-i.bak", "-f", str(script_path), str(index_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return True, "ok"
    except (OSError, subprocess.CalledProcessError) as exc:
        return False, f"sed_failed:{exc}"


def _restore_backup(index_path: Path) -> None:
    backup = Path(str(index_path) + ".bak")
    if backup.exists():
        index_path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")


def try_incremental_sed_update(
    *,
    index_path: Path,
    index: dict[str, Any],
    sources: Iterable[Path | str],
    project_name: str,
    framework: str,
    validate_index: callable,
) -> IncrementalUpdateResult:
    """Try reliable incremental update; return non-applied result on any risk.

    The function is intentionally conservative. It applies only when a single
    changed document can be patched with deterministic sed ranges.
    """
    if not index_path.exists():
        return IncrementalUpdateResult(False, "missing_index_file")

    docs = index.get("documents", [])
    postings = index.get("postings", {})
    if not isinstance(docs, list) or not isinstance(postings, dict):
        return IncrementalUpdateResult(False, "invalid_index_shape")

    source_paths = [Path(p) for p in sources if Path(p).exists()]
    doc_by_path: dict[str, dict[str, Any]] = {}
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        path = doc.get("path")
        if isinstance(path, str):
            doc_by_path[path] = doc

    source_set = {str(p) for p in source_paths}
    indexed_set = set(doc_by_path.keys())
    if source_set != indexed_set:
        return IncrementalUpdateResult(False, "source_set_changed")

    changed: list[Path] = []
    for path in source_paths:
        old_doc = doc_by_path.get(str(path))
        if not old_doc:
            return IncrementalUpdateResult(False, "missing_doc_entry")
        expected_hash = old_doc.get("source_hash")
        actual_hash = _source_hash(path)
        if not isinstance(expected_hash, str) or not actual_hash or expected_hash != actual_hash:
            changed.append(path)

    if len(changed) != 1:
        return IncrementalUpdateResult(False, "eligible_only_single_changed_doc")

    changed_path = changed[0]
    old_doc = doc_by_path[str(changed_path)]
    doc_id = old_doc.get("doc_id")
    if not isinstance(doc_id, int):
        return IncrementalUpdateResult(False, "invalid_doc_id")

    old_tf = _old_tf_for_doc(index, doc_id)
    built = _new_doc_and_tf(changed_path, doc_id)
    if built is None:
        return IncrementalUpdateResult(False, "changed_doc_unreadable_or_empty")
    new_doc, new_tf = built

    # Reliability gate: avoid structural postings adds/deletes in incremental mode.
    if set(old_tf.keys()) != set(new_tf.keys()):
        return IncrementalUpdateResult(False, "term_set_changed")

    # Build expected updated in-memory payload.
    updated = json.loads(json.dumps(index))
    for idx, doc in enumerate(updated["documents"]):
        if doc.get("doc_id") == doc_id:
            updated["documents"][idx] = new_doc
            break

    for term in sorted(old_tf.keys()):
        entries = updated["postings"].get(term, [])
        for entry in entries:
            if entry.get("doc_id") == doc_id:
                entry["tf"] = new_tf[term]
        entries.sort(key=lambda e: e.get("doc_id", -1))
        updated["postings"][term] = entries

    n_docs = int(updated.get("N", 0))
    old_len = int(old_doc.get("length", 0))
    new_len = int(new_doc.get("length", 0))
    avgdl = float(updated.get("avgdl", 0.0) or 0.0)
    if n_docs > 0:
        updated["avgdl"] = (avgdl * n_docs - old_len + new_len) / n_docs

    built_at = _now_iso()
    source_fingerprint = mi._documents_fingerprint(updated["documents"])
    index_build_id = hashlib.sha256(
        f"{source_fingerprint}|{built_at}|{len(source_paths)}".encode("utf-8")
    ).hexdigest()

    updated["built_at"] = built_at
    updated["source_fingerprint"] = source_fingerprint
    updated["index_build_id"] = index_build_id
    updated["project_name"] = project_name
    updated["framework"] = framework
    updated["source_count"] = len(source_paths)

    # Generate localized sed commands from current file layout.
    old_lines = index_path.read_text(encoding="utf-8").splitlines()
    commands: list[str] = []

    doc_range = _find_doc_inner_range(old_lines, doc_id)
    if doc_range is None:
        return IncrementalUpdateResult(False, "doc_anchor_not_found")
    dstart, dend = doc_range
    doc_lines = _format_doc_inner_lines(new_doc)
    commands.append(f"{dstart},{dend}c\\")
    for idx, line in enumerate(doc_lines):
        suffix = "\\" if idx < len(doc_lines) - 1 else ""
        commands.append(_sed_escape_line(line) + suffix)

    for term in sorted(old_tf.keys()):
        term_range = _find_term_range(old_lines, term)
        if term_range is None:
            return IncrementalUpdateResult(False, f"term_anchor_missing:{term}")
        tstart, tend, has_comma = term_range
        term_lines = _format_term_block(term, updated["postings"][term], has_comma)
        commands.append(f"{tstart},{tend}c\\")
        for idx, line in enumerate(term_lines):
            suffix = "\\" if idx < len(term_lines) - 1 else ""
            commands.append(_sed_escape_line(line) + suffix)

    meta_replacements = {
        '  "built_at": ': f'  "built_at": {json.dumps(updated["built_at"])},',
        '  "index_build_id": ': f'  "index_build_id": {json.dumps(updated["index_build_id"])},',
        '  "source_fingerprint": ': (
            f'  "source_fingerprint": {json.dumps(updated["source_fingerprint"])},'
        ),
        '  "avgdl": ': f'  "avgdl": {updated["avgdl"]},',
    }
    for prefix, replacement in meta_replacements.items():
        line_no = _find_line(old_lines, prefix)
        if line_no is None:
            return IncrementalUpdateResult(False, f"meta_anchor_missing:{prefix.strip()}")
        commands.append(f"{line_no}c\\")
        commands.append(_sed_escape_line(replacement))

    script_path = index_path.parent / "memory-index.incremental-update.sed"
    _write_sed_script(script_path, commands)

    ok, reason = _replace_with_sed(script_path, index_path)
    if not ok:
        _restore_backup(index_path)
        return IncrementalUpdateResult(False, reason)

    # Validate post-mutation payload and exact expected semantics.
    try:
        mutated = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _restore_backup(index_path)
        return IncrementalUpdateResult(False, "mutated_json_invalid")

    try:
        validate_index(mutated)
    except Exception:  # noqa: BLE001 — CH-24: ANY validation failure must roll the
        # mutation back (fail-safe); the incremental path then falls back to a full rebuild.
        _restore_backup(index_path)
        return IncrementalUpdateResult(False, "mutated_schema_invalid")

    if mutated != updated:
        _restore_backup(index_path)
        return IncrementalUpdateResult(False, "post_patch_mismatch")

    return IncrementalUpdateResult(True, "incremental_sed_applied")


__all__ = ["IncrementalUpdateResult", "try_incremental_sed_update"]
