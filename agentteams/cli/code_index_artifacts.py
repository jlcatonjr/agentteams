"""Code & API index artifacts (F-CODEIDX) — the gitignored local retrieval cache.

Carved from ``cli/artifacts.py`` (CH-07). That module stood at 976 lines against the 1000-line
ceiling with four siblings equally crowded, and the split was already latent: everything here sat
below a section rule separating it from the memory-index cluster, and the two halves differ in
kind — the memory index writes a **committed** artifact, this writes a **gitignored, rebuildable
cache** under ``references/code-index/``.

``cli.artifacts`` re-exports every name below, so existing import sites
(``cli/generate.py``, ``git_hooks.py``, ``build_team``, tests) resolve unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from agentteams.backup import BACKUP_DIR_NAME as _BACKUP_DIR_NAME
from agentteams.cli.schema_cache import (
    _load_schema_bytes,
    _schema_path,
    _validate_against_schema,
    _vcache_hit,
    _vcache_key,
    _vcache_store,
)
from agentteams.errors import CodeIndexError

# ---------------------------------------------------------------------------
# Code & API index (F-CODEIDX) — gitignored local retrieval cache
# ---------------------------------------------------------------------------
# The code index mirrors the memory-index helper cluster above, but writes a
# gitignored local cache (references/code-index/) rather than a committed
# artifact. Phase A emits the ``local-script`` partition; Phase B adds the
# ``api-module`` / ``api-doc`` partitions. See
# references/plans/code-api-vector-index.plan.md (audited v3).
CODE_INDEX_REL_DIR = "references/code-index"
_CODE_INDEX_LOCAL_EXTS = (".py", ".sh", ".bash")
# Directories never walked in the non-git fallback (RSR1 — no scratch/vendored).
_CODE_INDEX_DENY_DIRS = frozenset({
    ".git", ".venv", ".venv-ci", "node_modules", "__pycache__", "dist", "build",
    "_site", "tmp", _BACKUP_DIR_NAME, ".pytest_cache", ".mypy_cache", ".tox",
    "site-packages", ".eggs",
})


def _code_index_project_root(manifest: dict, output_dir: Path) -> Path:
    epp = manifest.get("existing_project_path")
    return Path(epp) if epp else output_dir.parent.parent


def _resolve_or_self(path: Path) -> Path:
    """``path.resolve()`` or the original path when resolution fails."""
    try:
        return path.resolve()
    except OSError:
        return path


def _within_root(path: Path, root_resolved: Path) -> bool:
    """True when *path*'s realpath stays inside *root_resolved* (symlink-safe)."""
    try:
        Path(os.path.realpath(path)).relative_to(root_resolved)
        return True
    except (ValueError, OSError):
        return False


def _extra_dir_candidates(project_root: Path, root_resolved: Path, raw: str) -> list[Path]:
    """Expand one ``code_index_extra_dirs`` entry to candidate script paths.

    A glob is expanded literally; a directory is scanned recursively for
    ``*.py``/``*.sh`` after confirming it stays within the project root. Any
    resolution error yields an empty list (no bare ``except: continue``).
    """
    try:
        if any(ch in raw for ch in "*?["):
            return sorted(project_root.glob(raw))
        target = project_root / raw
        if not (target.exists() and target.is_dir()):
            return []
        if not _within_root(target, root_resolved):
            return []
        found: list[Path] = []
        for ext in _CODE_INDEX_LOCAL_EXTS:
            found.extend(target.rglob(f"*{ext}"))
        return sorted(found)
    except (OSError, ValueError):
        return []


def _code_index_sources(manifest: dict, output_dir: Path) -> list[Path]:
    """Collect local-script source files for the code index (Phase A).

    RSR1-aware: durable, project-local sources only, never the gitignored
    scratch tree. Uses ``git ls-files -co --exclude-standard`` when the root is
    a git repo (so .gitignore — including ``tmp/`` — is honoured and untracked
    new scripts are still seen); falls back to a pruned walk otherwise.
    """
    import subprocess

    project_root = _code_index_project_root(manifest, output_dir)
    if not project_root.exists():
        return []
    paths: list[Path] = []
    try:
        out = subprocess.run(
            ["git", "-C", str(project_root), "ls-files", "-co",
             "--exclude-standard", "-z"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        for rel in out.split("\0"):
            if not rel:
                continue
            p = project_root / rel
            if p.suffix.lower() in _CODE_INDEX_LOCAL_EXTS and p.is_file():
                paths.append(p)
    except (OSError, subprocess.SubprocessError):
        for dirpath, dirnames, filenames in os.walk(project_root):
            dirnames[:] = [
                d for d in dirnames
                if d not in _CODE_INDEX_DENY_DIRS and not d.startswith(".")
            ]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower() in _CODE_INDEX_LOCAL_EXTS:
                    paths.append(p)
    # Consumer-declared extra script dirs/globs (mirrors memory_index_extra_dirs).
    # Safety: reject absolute paths, traversal escapes, and symlinked escapes.
    extra = manifest.get("code_index_extra_dirs")
    if isinstance(extra, list):
        root_resolved = _resolve_or_self(project_root)
        for raw in extra:
            if not isinstance(raw, str) or not raw.strip() or Path(raw).is_absolute():
                continue
            for c in _extra_dir_candidates(project_root, root_resolved, raw):
                if (
                    c.is_file()
                    and c.suffix.lower() in _CODE_INDEX_LOCAL_EXTS
                    and _within_root(c, root_resolved)
                ):
                    paths.append(c)

    # Deterministic order; de-dupe.
    seen: set[str] = set()
    unique: list[Path] = []
    for p in sorted(paths, key=lambda x: str(x)):
        s = str(p)
        if s not in seen:
            seen.add(s)
            unique.append(p)
    return unique


CODE_INDEX_VCACHE_REL_PATH = "references/code-index/.vcache"


def _code_index_schema_bytes() -> bytes:
    """Raw bytes of the trusted, package-bundled code-index schema (C3/C6)."""
    return _load_schema_bytes(_schema_path("code-index.schema.json"), CodeIndexError, "code index")


def _validate_code_index_schema(obj: dict) -> None:
    """Validate a code-index manifest or partition against its schema.

    A malformed payload raises ``CodeIndexError`` (a ``RuntimeError``, never
    ``OSError``) so callers can treat it as non-fatal, exactly like the memory
    index.
    """
    _validate_against_schema(
        obj, _code_index_schema_bytes(), error_cls=CodeIndexError, label="code index"
    )


def _write_code_index_partition(cache_dir: Path, name: str, part: dict) -> dict:
    """Validate + no-op-skip-write one partition; return the on-disk partition.

    A partition whose *content* is unchanged is not rewritten (its stable
    ``built_at`` is preserved — T6'), so a no-op refresh leaves the on-disk file
    byte-identical. Non-conformance raises ``CodeIndexError`` and writes nothing.
    """
    from agentteams import code_index as ci

    _validate_code_index_schema(part)
    path = cache_dir / f"{name}.json"
    existing = _load_json_or_none(path)
    if existing is not None and (
        ci.partition_content_fingerprint(existing) == ci.partition_content_fingerprint(part)
    ):
        return existing  # unchanged — keep stable file (and built_at)
    ci.atomic_write_json(path, part)
    return part


def _load_json_or_none(path: Path) -> dict | None:
    """Load a JSON object from *path*, or None when absent/unreadable/malformed."""
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_code_index(manifest: dict, output_dir: Path) -> Path:
    """Build + validate + atomically write the code-index cache. Returns the dir.

    Emits three partitions — ``local`` (repository scripts), ``api-modules`` and
    ``api-docs`` (the external APIs those scripts import; best-effort,
    metadata-only, never executing third-party code). The api partitions carry a
    dependency fingerprint so they can be detected stale on a dependency upgrade
    even when no local file changed (R2-M1). Non-conformance of any partition
    raises ``CodeIndexError`` and writes nothing for it; the caller treats it as
    non-fatal (the source files remain the source of truth).
    """
    from agentteams import code_index as ci
    from agentteams import code_sources as cs

    cache_dir = output_dir / CODE_INDEX_REL_DIR
    project_name = manifest.get("project_name", "")
    framework = manifest.get("framework", "")
    project_root = _code_index_project_root(manifest, output_dir)

    sources = _code_index_sources(manifest, output_dir)
    local_part = ci.build_code_partition(
        ci.local_units(sources), source_kind="local-script",
        project_name=project_name, framework=framework,
    )

    # Best-effort API collection (metadata-only; never raises).
    try:
        api = cs.collect_api(sources, project_root)
    except (OSError, ValueError, ImportError) as exc:  # never break a refresh on API resolution
        print(f"  !  Code index: API collection skipped ({exc}).", file=sys.stderr)
        api = cs.ApiCollection()

    api_mod_part = ci.build_code_partition(
        api.api_module_units, source_kind="api-module",
        project_name=project_name, framework=framework,
        dependency_fingerprint=api.dependency_fingerprint or None,
    )
    api_doc_part = ci.build_code_partition(
        api.api_doc_units, source_kind="api-doc",
        project_name=project_name, framework=framework,
        dependency_fingerprint=api.dependency_fingerprint or None,
    )

    on_disk = {
        "local": _write_code_index_partition(cache_dir, "local", local_part),
        "api-modules": _write_code_index_partition(cache_dir, "api-modules", api_mod_part),
        "api-docs": _write_code_index_partition(cache_dir, "api-docs", api_doc_part),
    }

    cache_manifest = ci.build_manifest(
        on_disk, project_name=project_name, framework=framework,
    )
    _validate_code_index_schema(cache_manifest)
    ci.atomic_write_json(cache_dir / "manifest.json", cache_manifest)

    if api.external_imports:
        print(
            f"     API: {len(api.resolved_source)}/{len(api.external_imports)} external "
            f"import(s) resolved to source; {api.declared_only_rate:.0%} declared-only.",
            file=sys.stderr,
        )
    return cache_dir


def _read_code_index(output_dir: Path) -> dict[str, Any]:
    """Load the cache manifest + partitions from ``references/code-index/``.

    Raises ``CodeIndexError`` when the cache is missing (run --refresh-code-index
    or --update first) or a file fails schema validation.
    """
    cache_dir = output_dir / CODE_INDEX_REL_DIR
    manifest_path = cache_dir / "manifest.json"
    if not manifest_path.exists():
        raise CodeIndexError(
            f"code index not found at {cache_dir}; run --refresh-code-index or --update first"
        )
    try:
        manifest_bytes = manifest_path.read_bytes()
        cache_manifest = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise CodeIndexError(f"failed reading code index manifest at {manifest_path}: {exc}") from exc

    # Read the manifest + every partition (bytes + parsed). Iteration order for
    # the returned dict is preserved (ranking may tie-break on it); the cache key
    # folds the bytes in a STABLE (sorted-name) order so it is independent of it.
    partitions: dict[str, dict] = {}
    part_bytes_by_name: dict[str, bytes] = {}
    for name, meta in cache_manifest.get("partitions", {}).items():
        part_path = cache_dir / meta.get("file", f"{name}.json")
        if not part_path.exists():
            continue
        try:
            part_bytes = part_path.read_bytes()
            part = json.loads(part_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            raise CodeIndexError(f"failed reading code index partition {part_path}: {exc}") from exc
        partitions[name] = part
        part_bytes_by_name[name] = part_bytes

    # Skip the (repeated, expensive) schema pass over the manifest AND every
    # partition when the sidecar records a prior success for exactly these bytes
    # + schema — the same content-hash cache proven on the memory-index hot path.
    # Fail-open, self-healing, gitignored (lives inside references/code-index/).
    # A change to ANY file OR the schema changes the key and forces a re-validate.
    schema_bytes = _code_index_schema_bytes()
    chunks = [manifest_bytes] + [part_bytes_by_name[n] for n in sorted(part_bytes_by_name)]
    sidecar = output_dir / CODE_INDEX_VCACHE_REL_PATH
    key = _vcache_key(chunks, schema_bytes)
    if not _vcache_hit(sidecar, key, "code-index-vcache"):
        _validate_against_schema(
            cache_manifest, schema_bytes, error_cls=CodeIndexError, label="code index"
        )
        for part in partitions.values():
            _validate_against_schema(
                part, schema_bytes, error_cls=CodeIndexError, label="code index"
            )
        _vcache_store(sidecar, key, "code-index-vcache")  # only after all pass (C-L2)
    return {"manifest": cache_manifest, "partitions": partitions}


def _run_refresh_code_index(manifest: dict, output_dir: Path) -> int:
    """Rebuild the code-index cache only (no template emit/update path)."""
    cache_dir = _write_code_index(manifest, output_dir)
    data = _read_code_index(output_dir)
    partitions = data["partitions"]
    total = sum(int(p.get("N", 0)) for p in partitions.values())
    print(f"  ✓  Refreshed code index: {cache_dir}")
    print(f"     Indexed {total} unit(s) across {len(partitions)} partition(s) "
          f"(gitignored local cache).")
    return 0


def _run_query_code_index(
    manifest: dict,
    output_dir: Path,
    query: str,
    k: int,
    strategy: str = "lexical",
    kind: str = "all",
) -> int:
    """Query the code-index cache and print ranked hits (auto-refresh if stale)."""
    from agentteams import code_index as ci
    from agentteams import code_sources as cs

    data = _read_code_index(output_dir)
    partitions = data["partitions"]
    sources = _code_index_sources(manifest, output_dir)
    project_root = _code_index_project_root(manifest, output_dir)

    # Local staleness: source hash/mtime. API staleness: dependency fingerprint
    # (a dependency upgrade is invisible to local mtimes — R2-M1). Compute the
    # api fingerprint only when the query touches an api kind, to keep local
    # queries fast.
    stale = False
    local_part = partitions.get("local")
    if local_part is not None and ci.is_partition_stale(local_part, sources):
        stale = True
    if not stale and kind in ("api", "doc", "all"):
        try:
            dep_fp = cs.compute_dependency_fingerprint(sources, project_root)
        except (OSError, ValueError):
            dep_fp = None
        if dep_fp is not None:
            for name, part in partitions.items():
                if part.get("source_kind") in ("api-module", "api-doc") and \
                        ci.is_partition_stale(part, dependency_fingerprint=dep_fp):
                    stale = True
                    break
    if stale:
        _write_code_index(manifest, output_dir)
        data = _read_code_index(output_dir)
        partitions = data["partitions"]
        print("  !  Code index was stale relative to sources/dependencies. Auto-refreshed.")

    hits = ci.query_partitions(partitions, query, k=k, strategy=strategy, kind=kind)
    print(f"Query: {query!r}  (kind={kind}, strategy={strategy})")
    if not hits:
        print("  No matching code found.")
        return 1
    for idx, hit in enumerate(hits, start=1):
        label = hit.get("symbol") or hit["title"]
        print(
            f"  {idx}. score={hit['score']:.6f} confidence={hit['confidence']}  [{hit['source_kind']}] {label}\n"
            f"     path: {hit['path']}\n"
            f"     snippet: {hit['snippet']}"
        )
    return 0



def _refresh_existing_code_index(manifest: dict, output_dir: Path) -> None:
    """On --update, keep an *existing* gitignored code-index cache fresh (lazy).

    Never creates the cache — ``--query-code`` is the primary trigger; this only
    prevents an already-used cache from going stale after an update. Non-fatal.
    """
    if not (output_dir / CODE_INDEX_REL_DIR / "manifest.json").exists():
        return
    try:
        _write_code_index(manifest, output_dir)
    except (OSError, CodeIndexError) as exc:
        print(f"  !  Code index refresh failed: {exc}", file=sys.stderr)
