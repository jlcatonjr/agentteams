"""Deterministic emission baselines for regression snapshots.

A *baseline* is a JSON manifest of relative file paths and their SHA-256
content hashes, captured from a target output tree (a generated agent team).
Baselines are compared byte-for-byte across runs to detect emission drift.

This module is intentionally tiny and dependency-free. It is invoked by
``--capture-baseline`` and ``--check-baseline`` (added in Phase 0) and by the
regression test runner. It avoids name collision with
``framework_research.refresh_snapshot`` (which captures upstream docs, not
emission output).

Notes
-----
- Hash inputs are raw file bytes only. Timestamps, mtime, and filesystem
  ordering are excluded so baselines are deterministic across machines.
- Directories are walked in sorted order. Symlinks are not followed.
- A baseline includes a ``schema_version`` so older baselines remain
  comparable as the format evolves.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

BASELINE_SCHEMA_VERSION = "1.0"

# Files and directories excluded from baseline capture. These are runtime
# state, caches, or user-mutable artifacts unrelated to emission determinism.
_DEFAULT_EXCLUDE_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".DS_Store",
    }
)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(root: Path, exclude: frozenset[str]) -> list[Path]:
    """Return every regular file under ``root`` in deterministic order."""
    if not root.exists():
        return []
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if any(part in exclude for part in p.parts):
            continue
        if p.is_file() and not p.is_symlink():
            out.append(p)
    return out


def capture(
    root: Path,
    *,
    label: str,
    exclude: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Compute a baseline manifest for ``root``.

    Parameters
    ----------
    root : Path
        Directory tree to snapshot.
    label : str
        Free-form identifier (e.g. ``copilot-vscode``, ``bridge-overlay``).
    exclude : frozenset[str] | None
        Path components to skip. Defaults to caches and VCS metadata.
    """
    exclude_set = exclude or _DEFAULT_EXCLUDE_NAMES
    root = root.resolve()
    files: list[dict[str, str]] = []
    for fp in _iter_files(root, exclude_set):
        rel = fp.relative_to(root).as_posix()
        files.append({"path": rel, "sha256": _hash_file(fp)})
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "label": label,
        "root": root.as_posix(),
        "file_count": len(files),
        "files": files,
    }


def write(manifest: dict[str, Any], out_path: Path) -> None:
    """Write a baseline manifest as deterministic JSON."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def diff(prior: dict[str, Any], current: dict[str, Any]) -> dict[str, list[str]]:
    """Compare two baselines; return added / removed / changed file lists."""
    prior_files = {entry["path"]: entry["sha256"] for entry in prior.get("files", [])}
    curr_files = {entry["path"]: entry["sha256"] for entry in current.get("files", [])}
    added = sorted(set(curr_files) - set(prior_files))
    removed = sorted(set(prior_files) - set(curr_files))
    changed = sorted(
        p for p in set(prior_files) & set(curr_files) if prior_files[p] != curr_files[p]
    )
    return {"added": added, "removed": removed, "changed": changed}


__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "capture",
    "diff",
    "load",
    "write",
]
