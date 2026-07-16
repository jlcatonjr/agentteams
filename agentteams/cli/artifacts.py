"""artifacts.py — delivery-receipt / eval-suite / model-routing / memory-index writers.

Extracted verbatim from build_team.py (CH-07) except the 5 schema paths are
re-anchored from `Path(__file__).resolve().parent` (build_team at repo root)
to `parents[2]` (this module at agentteams/cli/) — the same repo-root/schemas
dir. build_team re-exports these so main and tests resolve them unchanged.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentteams.atomicio import _atomic_write_text
# Re-exported for build_team's namespace (see module docstring) and tests:
# _require_jsonschema and _SCHEMA_VALIDATOR_CACHE were carved into schema_cache
# but their old import sites resolve through agentteams.cli.artifacts.
from agentteams.cli.schema_cache import (
    _SCHEMA_VALIDATOR_CACHE,
    _load_schema_bytes,
    _require_jsonschema,
    _schema_path,
    _validate_against_schema,
    _validate_cached,
    _vcache_hit,
    _vcache_key,
    _vcache_store,
)
from agentteams.errors import (
    CodeIndexError,
    DeliveryReceiptError,
    EvalSuiteError,
    MemoryIndexError,
    ModelRoutingError,
)

if TYPE_CHECKING:
    from agentteams.mcp_emit import MCPEmissionResult

def _compute_file_hashes(written_abs_paths: list[str], output_dir: Path) -> dict[str, str]:
    """Return a mapping of relative path → 16-char SHA-256 hex for written files.

    Paths are stored relative to output_dir so the build-log is portable.
    """
    import hashlib
    hashes: dict[str, str] = {}
    for abs_path_str in written_abs_paths:
        abs_path = Path(abs_path_str)
        if not abs_path.exists():
            continue
        try:
            rel = str(abs_path.relative_to(output_dir))
        except ValueError:
            # File is outside output_dir (e.g. ../copilot-instructions.md)
            try:
                rel = str(abs_path.relative_to(output_dir.parent))
                rel = "../" + rel
            except ValueError:
                rel = abs_path_str
        digest = hashlib.sha256(abs_path.read_bytes()).hexdigest()[:16]
        hashes[rel] = digest
    return hashes
DELIVERY_RECEIPT_REL_PATH = "references/delivery-receipt.json"
def _write_delivery_receipt(manifest: dict, output_dir: Path) -> Path:
    """Write a P3 delivery receipt attesting that ``--update`` succeeded.

    The receipt is written AFTER the build-log (``_write_run_log``) inside the
    same ``not args.dry_run and result.success`` block, so its
    ``manifest_fingerprint`` always matches the build-log just written. This is
    the "heal first, attest second" ordering (see R3 rationale in
    ``docs_src/delivery-procedure.md``). If the receipt write
    fails after the log is written, the next ``--update`` converges to zero
    drift and re-emits the receipt — the safe failure direction.

    The receipt is excluded from drift detection by construction: it is never
    added to the rendered set, ``output_files``, ``template_hashes``, or
    ``file_hashes``. See ``schemas/delivery-receipt.schema.json`` for the
    contract; see ``docs_src/delivery-procedure.md`` for the procedure and the
    "heal first, attest second" (R3) ordering rationale.

    The payload is validated against ``schemas/delivery-receipt.schema.json``
    at write time (RA2); a non-conforming receipt raises
    ``DeliveryReceiptError`` and is *not* written. Callers treat that as
    non-fatal — the build-log heal stands and the next ``--update`` re-emits.
    """
    from datetime import datetime, timezone
    from agentteams import drift as _drift
    try:
        from agentteams import __version__ as _agentteams_version
    except (ImportError, AttributeError):  # version attr legitimately absent
        _agentteams_version = None

    receipt: dict[str, object] = {
        "artifact_type": "delivery-receipt",
        "receipt_schema_version": "1.0",
        "delivered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project_name": manifest.get("project_name", ""),
        "framework": manifest.get("framework", ""),
        "manifest_fingerprint": _drift.compute_manifest_fingerprint(manifest),
        "fingerprint_algo_version": _drift.FINGERPRINT_ALGO_VERSION,
        "output_dir": str(output_dir),
    }
    if _agentteams_version:
        receipt["agentteams_version"] = str(_agentteams_version)

    # RA2: validate against the shipped schema before writing. A non-conforming
    # receipt is a real defect we want surfaced — not silently written. A
    # missing jsonschema module degrades to a non-fatal DeliveryReceiptError
    # (see _require_jsonschema) rather than crashing a completed merge.
    schema_bytes = _load_schema_bytes(
        _schema_path("delivery-receipt.schema.json"), DeliveryReceiptError, "delivery receipt"
    )
    _validate_against_schema(
        receipt, schema_bytes, error_cls=DeliveryReceiptError, label="delivery receipt"
    )

    receipt_path = output_dir / DELIVERY_RECEIPT_REL_PATH
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt_path
EVAL_SUITE_REL_PATH = "references/eval-suite.json"
def _write_eval_suite(manifest: dict, output_dir: Path) -> Path:
    """Emit the framework-neutral eval suite (Cluster A Phase 2, increment 1).

    Mirrors ``_write_delivery_receipt``: build from the manifest, validate
    against ``schemas/eval-suite.schema.json`` before writing, raise
    ``EvalSuiteError`` (a RuntimeError, never OSError) on non-conformance and
    write nothing. Generator-owned artifact at
    ``<output_dir>/references/eval-suite.json``; excluded from drift by
    construction (never added to the rendered set, output_files_map,
    template_hashes, or file_hashes; never read by --check or --update). See
    ``schemas/eval-suite.schema.json`` and ``docs_src`` for the contract.
    """
    from agentteams.eval_suite import build_eval_suite

    suite = build_eval_suite(manifest)

    schema_bytes = _load_schema_bytes(
        _schema_path("eval-suite.schema.json"), EvalSuiteError, "eval suite"
    )
    _validate_against_schema(suite, schema_bytes, error_cls=EvalSuiteError, label="eval suite")

    suite_path = output_dir / EVAL_SUITE_REL_PATH
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    suite_path.write_text(json.dumps(suite, indent=2) + "\n", encoding="utf-8")
    return suite_path
MODEL_ROUTING_REL_PATH = "references/model-routing.json"
def _write_model_routing(manifest: dict, output_dir: Path) -> Path:
    """Emit the framework-neutral model-routing contract (F6, opt-in).

    Called ONLY when ``--cost-routing`` is set. Same RA2 contract as
    ``_write_eval_suite``: pure build → schema-validate against
    ``schemas/model-routing.schema.json`` → raise ``ModelRoutingError``
    (RuntimeError, never OSError) and write nothing on non-conformance.
    Generator-owned, drift-excluded by construction (``.json``; never in
    output_files_map/template_hashes/file_hashes; never read by --check or
    --update). Does NOT modify any rendered agent file.
    """
    from agentteams.model_routing import build_routing_contract

    contract = build_routing_contract(manifest)

    schema_bytes = _load_schema_bytes(
        _schema_path("model-routing.schema.json"), ModelRoutingError, "model-routing contract"
    )
    _validate_against_schema(
        contract, schema_bytes, error_cls=ModelRoutingError, label="model-routing contract"
    )

    contract_path = output_dir / MODEL_ROUTING_REL_PATH
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    return contract_path


def _write_mcp_servers(manifest: dict, project_root: Path) -> "MCPEmissionResult":
    """Emit the INERT ``.claude/mcp-servers.agentteams.json`` (opt-in).

    Called only when an MCP host-feature token is enabled and the manifest carries
    operator-SPECIFIED ``mcp_servers[]`` (report §5.4/§6). Unlike the sibling
    writers, the output base is the PROJECT ROOT (not the agents ``output_dir``)
    because this is a Claude-Code-host config location, deliberately NOT named
    ``.mcp.json`` — it provisions nothing.

    Reuses ``agentteams.mcp_emit.emit_mcp_artifact``, which validates each server
    against ``schemas/mcp-server.schema.json``, refuses inline-secret-shaped
    ``credential_ref`` values, records ``activation_status`` fail-closed, and
    defaults ``overwrite=False`` so operator authorization records are never
    clobbered on re-run (the refresh-vs-never-clobber rule, report §6.4). Returns
    the ``MCPEmissionResult`` so the caller can surface written/blocked/errors.
    Drift-excluded by construction (``.json``; never in
    output_files_map/template_hashes/file_hashes).
    """
    from agentteams.mcp_emit import emit_mcp_artifact

    return emit_mcp_artifact(
        servers=manifest.get("mcp_servers", []) or [],
        features=manifest.get("host_features", []) or [],
        output_root=project_root,
    )


def _emit_mcp_servers_if_enabled(manifest: dict, project_root: Path) -> None:
    """Emit the inert MCP server artifact when an MCP host-feature token is on.

    Opt-in mirror of the ``_write_model_routing`` gate: fires only when
    ``mcp_enabled(host_features)`` AND the manifest carries operator-specified
    ``mcp_servers[]``. Best-effort like the sibling artifact writers — never
    raises into the build. Surfaces what was written, which servers still need
    operator security authorization before activation, and any non-conformant
    servers that were skipped.
    """
    from agentteams.mcp_emit import mcp_enabled

    features = manifest.get("host_features", []) or []
    if not mcp_enabled(features) or not manifest.get("mcp_servers"):
        return
    try:
        res = _write_mcp_servers(manifest, project_root)
    except OSError as exc:
        print(f"  !  MCP server artifact write failed: {exc}", file=sys.stderr)
        return
    for path in res.written:
        print(f"  ✓  Emitted inert MCP server definitions: {path}")
    if res.activation_blocked:
        print(
            "  ⚠  MCP servers needing operator security authorization before "
            f"activation: {', '.join(res.activation_blocked)}"
        )
    for err in res.errors:
        print(f"  !  MCP server skipped (non-conformant): {err}", file=sys.stderr)
MEMORY_INDEX_REL_PATH = "references/memory-index.json"
MEMORY_INDEX_EXTRA_DOC_NAMES = ("CHANGELOG.md", "README.md", "build-team-plan.md")
def _memory_index_sources(manifest: dict, output_dir: Path) -> list[Path]:
    """Collect durable text sources for the memory index (F8).

    RSR1-aware: durable, project-local sources only — never gitignored
    scratch areas. Prefers the manifest's ``existing_project_path`` (the
    operator's explicit signal of the project root, e.g. when ``--output``
    is non-standard); falls back to inferring from ``output_dir`` when
    absent (standard layout: ``<project>/.github/agents`` or
    ``<project>/.claude/agents``).
    """
    epp = manifest.get("existing_project_path")
    project_root = Path(epp) if epp else output_dir.parent.parent
    sources: list[Path] = []
    # Work summaries (the canonical durable history substrate).
    ws = project_root / "workSummaries"
    if ws.exists() and ws.is_dir():
        sources.extend(sorted(ws.rglob("*.md")))
    # Top-level durable docs.
    for name in MEMORY_INDEX_EXTRA_DOC_NAMES:
        p = project_root / name
        if p.exists() and p.is_file():
            sources.append(p)
    # Additional durable authored docs.
    docs_src = project_root / "docs_src"
    if docs_src.exists() and docs_src.is_dir():
        sources.extend(sorted(docs_src.glob("*.md")))
    refs = project_root / "references"
    if refs.exists() and refs.is_dir():
        sources.extend(sorted(refs.rglob("*.md")))
    # Consumer-declared extra index dirs / globs (W22 recall-first follow-up).
    # Each entry is a project-relative string treated as:
    #   - a glob pattern if it contains '*' or '?' (expanded literally), or
    #   - a directory otherwise (recursively scanned for *.md).
    # Safety: reject absolute paths, traversal that escapes project_root, and
    # symlinked escapes (post-glob realpath check).
    extra = manifest.get("memory_index_extra_dirs")
    if isinstance(extra, list):
        try:
            project_root_resolved = project_root.resolve()
        except OSError:
            project_root_resolved = project_root
        for raw in extra:
            if not isinstance(raw, str) or not raw.strip():
                continue
            if Path(raw).is_absolute():
                continue
            is_glob = any(ch in raw for ch in "*?[")
            try:
                if is_glob:
                    candidates = sorted(project_root.glob(raw))
                else:
                    target = (project_root / raw)
                    if not (target.exists() and target.is_dir()):
                        continue
                    try:
                        target.resolve().relative_to(project_root_resolved)
                    except (ValueError, OSError):
                        continue
                    candidates = sorted(target.rglob("*.md"))
            except (OSError, ValueError):
                continue
            for c in candidates:
                if not c.is_file() or c.suffix != ".md":
                    continue
                try:
                    real = Path(os.path.realpath(c))
                    real.relative_to(project_root_resolved)
                except (ValueError, OSError):
                    continue
                sources.append(c)
    return sources
MEMORY_INDEX_VCACHE_REL_PATH = "references/memory-index.vcache"


def _memory_index_schema_path() -> Path:
    """The trusted, package-bundled memory-index schema (monkeypatched in tests
    to simulate a schema/package upgrade)."""
    return _schema_path("memory-index.schema.json")


def _validate_memory_index_bytes(
    index: dict[str, object], index_bytes: bytes, output_dir: Path | None
) -> None:
    """Validate *index* against the bundled schema, consulting the sidecar cache
    when *output_dir* is given. Central validator for both read and write paths;
    ``output_dir=None`` forces a full, uncached validation.

    ``index_bytes`` must be the exact bytes read from / to be written to disk so
    the key matches on the next read (C5).
    """
    schema_bytes = _load_schema_bytes(
        _memory_index_schema_path(), MemoryIndexError, "memory index"
    )
    sidecar = (output_dir / MEMORY_INDEX_VCACHE_REL_PATH) if output_dir is not None else None
    _validate_cached(
        index, [index_bytes], schema_bytes,
        error_cls=MemoryIndexError, label="memory index",
        sidecar_path=sidecar, artifact_type="memory-index-vcache",
    )


def _read_memory_index(output_dir: Path) -> dict[str, object]:
    """Load and parse references/memory-index.json from output_dir.

    Raises RuntimeError when the file is missing or invalid.
    """
    index_path = output_dir / MEMORY_INDEX_REL_PATH
    if not index_path.exists():
        raise MemoryIndexError(
            f"memory index not found at {index_path}; run --refresh-index or --update first"
        )
    try:
        raw = index_path.read_bytes()
        index = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryIndexError(f"failed reading memory index at {index_path}: {exc}") from exc
    _validate_memory_index_bytes(index, raw, output_dir)
    return index


def _validate_memory_index_schema(index: dict[str, object]) -> None:
    """Validate a parsed memory-index payload against its schema (no cache).

    Retained as the incremental-update path's ``validate_index`` callback, which
    is invoked with only the parsed dict. Always runs a full validation
    (fail-open); the cached fast path is :func:`_validate_memory_index_bytes`.
    """
    _validate_memory_index_bytes(index, b"", None)
def _run_refresh_index(manifest: dict, output_dir: Path) -> int:
    """Rebuild memory-index.json only (no template emit/update path)."""
    path = _write_memory_index(manifest, output_dir)
    index = _read_memory_index(output_dir)
    docs = int(index.get("N", 0))
    source_count = int(index.get("source_count", 0))
    print(f"  ✓  Refreshed memory index: {path}")
    print(f"     Indexed {docs} document(s) from {source_count} source file(s).")
    return 0
def _run_query_index(
    manifest: dict, output_dir: Path, query: str, k: int, strategy: str = "lexical"
) -> int:
    """Query memory-index.json and print ranked hits."""
    from agentteams.memory_index import is_index_stale, query_index

    index = _read_memory_index(output_dir)
    sources = _memory_index_sources(manifest, output_dir)
    if is_index_stale(index, sources):
        refreshed_path = _write_memory_index(manifest, output_dir)
        index = _read_memory_index(output_dir)
        print(
            "  !  Index was stale relative to source files. "
            f"Auto-refreshed: {refreshed_path}"
        )

    hits = query_index(index, query, k=k, strategy=strategy)

    print(f"Query: {query!r}")
    if not hits:
        print("  No matching documents found.")
        return 1
    for idx, hit in enumerate(hits, start=1):
        print(
            f"  {idx}. score={hit['score']:.6f}  {hit['title']}\n"
            f"     path: {hit['path']}\n"
            f"     snippet: {hit['snippet']}"
        )
    return 0
def _write_memory_index(manifest: dict, output_dir: Path) -> Path:
    """Emit the additive lexical memory index (F8).

    Always emitted (no opt-in flag): the index is *additive* to the existing
    work-summary documents, never a replacement. Empty source list ⇒ an
    empty-but-schema-valid index (a freshly generated team has no history
    yet; later --update runs accumulate it). Same RA2 contract as the other
    generator-owned artifacts: pure build → schema-validate at write time →
    raise ``MemoryIndexError`` (RuntimeError, never OSError) on
    non-conformance, write nothing → non-fatal at the call site →
    drift-excluded by construction.
    """
    from agentteams.memory_index import build_memory_index
    from agentteams.memory_index_incremental import try_incremental_sed_update

    index_path = output_dir / MEMORY_INDEX_REL_PATH
    incremental_enabled = os.getenv("AGENTTEAMS_MEMORY_INDEX_INCREMENTAL_SED", "").strip() == "1"

    if incremental_enabled and index_path.exists():
        try:
            current = _read_memory_index(output_dir)
            result = try_incremental_sed_update(
                index_path=index_path,
                index=current,
                sources=_memory_index_sources(manifest, output_dir),
                project_name=manifest.get("project_name", ""),
                framework=manifest.get("framework", ""),
                validate_index=_validate_memory_index_schema,
            )
            if result.applied:
                return index_path
            print(
                "  !  Incremental memory-index update skipped "
                f"({result.reason}); falling back to full rebuild."
            )
        except (OSError, MemoryIndexError, RuntimeError) as exc:
            print(
                "  !  Incremental memory-index update failed "
                f"({exc}); falling back to full rebuild."
            )

    index = build_memory_index(
        _memory_index_sources(manifest, output_dir),
        project_name=manifest.get("project_name", ""),
        framework=manifest.get("framework", ""),
    )

    serialized = json.dumps(index, indent=2) + "\n"
    index_bytes = serialized.encode("utf-8")
    _validate_memory_index_bytes(index, index_bytes, output_dir)

    # Atomic write via the shared atomicio primitive (temp-in-same-dir + fsync +
    # os.replace) — a crash never leaves a torn index for a concurrent
    # --query-index to read+validate. Writes the exact bytes hashed above, so the
    # next read hits the validation cache (C5).
    _atomic_write_text(index_path, serialized)
    return index_path


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
    "_site", "tmp", ".agentteams-backups", ".pytest_cache", ".mypy_cache", ".tox",
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
            f"  {idx}. score={hit['score']:.6f}  [{hit['source_kind']}] {label}\n"
            f"     path: {hit['path']}\n"
            f"     snippet: {hit['snippet']}"
        )
    return 0


def _run_retrieval_utility_modes(args: Any, manifest: dict, output_dir: Path) -> int | None:
    """Dispatch the memory-index and code-index utility modes (no template render).

    Returns an exit code when one of ``--refresh-index`` / ``--query-index`` /
    ``--refresh-code-index`` / ``--query-code`` ran, else ``None`` so the caller
    continues the normal generate/update flow.
    """
    if getattr(args, "refresh_index", False):
        try:
            return _run_refresh_index(manifest, output_dir)
        except (OSError, MemoryIndexError) as exc:
            print(f"Memory index refresh failed: {exc}", file=sys.stderr)
            return 1
    if getattr(args, "query_index", None):
        try:
            return _run_query_index(
                manifest, output_dir, args.query_index, args.query_k,
                strategy=args.query_strategy,
            )
        except (OSError, MemoryIndexError) as exc:
            print(f"Memory index query failed: {exc}", file=sys.stderr)
            return 1
    if getattr(args, "refresh_code_index", False):
        try:
            return _run_refresh_code_index(manifest, output_dir)
        except (OSError, CodeIndexError) as exc:
            print(f"Code index refresh failed: {exc}", file=sys.stderr)
            return 1
    if getattr(args, "query_code", None):
        try:
            return _run_query_code_index(
                manifest, output_dir, args.query_code, args.code_query_k,
                strategy=args.code_query_strategy, kind=args.code_kind,
            )
        except (OSError, CodeIndexError) as exc:
            print(f"Code index query failed: {exc}", file=sys.stderr)
            return 1
    return None


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
