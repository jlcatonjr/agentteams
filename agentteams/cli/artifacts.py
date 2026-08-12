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
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentteams.atomicio import _atomic_write_text
from agentteams.backup import _BACKUP_DIR_NAME
from agentteams.backup import BACKUP_DIR_NAME as _BACKUP_DIR_NAME

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
    from agentteams.codex_mcp_emit import CodexMCPEmissionResult
    from agentteams.mcp_emit import MCPEmissionResult

def _compute_file_hashes(written_abs_paths: list[str], output_dir: Path) -> dict[str, str]:
    """Return a mapping of relative path → 16-char SHA-256 hex for written files.

    Paths are stored relative to output_dir so the build-log is portable.
    """
    import hashlib
    import os
    hashes: dict[str, str] = {}
    for abs_path_str in written_abs_paths:
        abs_path = Path(abs_path_str)
        if not abs_path.exists():
            continue
        try:
            rel = str(abs_path.relative_to(output_dir))
        except ValueError:
            # File is outside output_dir (e.g. ../copilot-instructions.md, or
            # further out still, e.g. .vscode/tasks.json) — os.path.relpath
            # handles any number of ../ segments, unlike relative_to, so the
            # build-log never falls back to a raw absolute path (that would
            # leak the operator's home-directory username into a tracked file).
            rel = os.path.relpath(abs_path, output_dir)
        digest = hashlib.sha256(abs_path.read_bytes()).hexdigest()[:16]
        hashes[rel] = digest
    return hashes
DELIVERY_RECEIPT_REL_PATH = "references/delivery-receipt.json"




def _read_front_matter_or_empty(path: Path) -> dict[str, str]:
    """Return a file's front-matter keys, or ``{}`` when it cannot be read.

    Return-empty rather than except-and-continue: CH-24's ratchet
    (``tests/test_code_hygiene.py::SWALLOW_BASELINE``) counts ``except`` clauses whose body is
    only ``pass``/``continue``, and an unreadable file during baseline collection does not need
    to be one of them.

    Args:
        path: The emitted file to read.

    Returns:
        ``{key: value}``, empty when the file is unreadable.
    """
    from agentteams.fences import _front_matter_keys

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return _front_matter_keys(text)

def _compute_front_matter_baseline(
    written_abs_paths: list[str], output_dir: Path
) -> dict[str, dict[str, str]]:
    """Record each emitted file's front matter as written, for three-way merge on later updates.

    Args:
        written_abs_paths: Absolute paths of every file this run wrote, merged or left unchanged.
        output_dir: The agents directory, used to relativise keys.

    Returns:
        ``{rel_path: {key: value}}`` for files that have front matter; files without one are
        omitted rather than stored empty, so absence stays distinguishable from "no keys".
    """
    baseline: dict[str, dict[str, str]] = {}
    for abs_path in written_abs_paths:
        path = Path(abs_path)
        if path.suffix != ".md" or not path.is_file():
            continue
        keys = _read_front_matter_or_empty(path)
        if keys:
            try:
                rel = str(path.relative_to(output_dir))
            except ValueError:
                rel = str(path)
            baseline[rel] = keys
    return baseline

def _sanitized_output_dir(output_dir: Path) -> str:
    """Return a receipt-safe representation of *output_dir*.

    Repo-relative when *output_dir* is inside a git repository (walks up looking
    for `.git`), else just the directory's own name. Never returns an absolute
    path — an absolute path embeds the operator's home directory / OS username,
    which is fine for a private consumer output dir but leaks real machine
    identity when the receipt lands in a tracked, published artifact (e.g. this
    repository's own `examples/*/expected/` fixtures). The schema documents
    `output_dir` as "absolute or repo-relative... informational only", so a
    repo-relative value is fully conformant.
    """
    resolved = output_dir.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return str(resolved.relative_to(candidate))
    return resolved.name


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
        "output_dir": _sanitized_output_dir(output_dir),
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
    _atomic_write_text(receipt_path, json.dumps(receipt, indent=2) + "\n")
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
    _atomic_write_text(suite_path, json.dumps(suite, indent=2) + "\n")
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
    _atomic_write_text(contract_path, json.dumps(contract, indent=2) + "\n")
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


def _write_codex_mcp_servers(manifest: dict, project_root: Path) -> "CodexMCPEmissionResult":
    """Splice wirable MCP servers into ``.codex/config.toml`` (open-items
    remediation OPEN-6). Output base is the PROJECT ROOT, same convention as
    ``_write_mcp_servers``, since this is a Codex-host config location.

    Unlike the Claude sidecar, ``.codex/config.toml`` is a real, live config
    Codex reads to launch servers — reuses ``codex_mcp_emit.emit_codex_mcp_config``,
    which applies Goose's stricter first-party/read-only/no-review-required
    auto-wire bar rather than Claude's inert-write bar, and text-splices only
    the ``[mcp_servers.*]`` tables. A content-preservation check verifies the
    rest of a hand-authored config.toml (sandbox/profile settings, comments)
    is unchanged before writing, refusing the write rather than risk silent
    data loss if not.
    """
    from agentteams.codex_mcp_emit import emit_codex_mcp_config

    return emit_codex_mcp_config(
        servers=manifest.get("mcp_servers", []) or [],
        features=manifest.get("host_features", []) or [],
        output_root=project_root,
    )


def _emit_host_mcp_artifacts_if_enabled(manifest: dict, project_root: Path) -> None:
    """Emit every host's MCP artifact whose feature token is active — the one
    call site's worth of pipeline wiring, so adding a future host's MCP
    emitter never means touching generate.py's 3 call sites again."""
    _emit_mcp_servers_if_enabled(manifest, project_root)
    _emit_codex_mcp_if_enabled(manifest, project_root)


def _emit_codex_mcp_if_enabled(manifest: dict, project_root: Path) -> None:
    """Emit the Codex MCP config splice when the codex:mcp host-feature token is on.

    Mirrors ``_emit_mcp_servers_if_enabled``'s gate/best-effort shape. Fires
    independently of which ``--framework`` this generate call is rendering —
    like the Claude sidecar, this is a host-scoped artifact (which local
    tools read MCP config from), not a per-framework rendering output.
    """
    from agentteams.codex_mcp_emit import codex_mcp_enabled

    features = manifest.get("host_features", []) or []
    if not codex_mcp_enabled(features) or not manifest.get("mcp_servers"):
        return
    try:
        res = _write_codex_mcp_servers(manifest, project_root)
    except OSError as exc:
        print(f"  !  Codex MCP config write failed: {exc}", file=sys.stderr)
        return
    for path in res.written:
        print(f"  ✓  Spliced Codex MCP server config: {path}")
    if res.not_wired:
        blocked = ", ".join(f"{sid} ({reason})" for sid, reason in res.not_wired.items())
        print(f"  ⚠  MCP servers not auto-wired into config.toml: {blocked}")
    if res.dropped_unmanaged:
        print(
            "  ⚠  Pre-existing [mcp_servers.*] entries not declared to agentteams were "
            f"replaced: {', '.join(res.dropped_unmanaged)} (hand-author these outside "
            "the agentteams-managed block, or add them to mcp_servers[] to keep them)"
        )
    for err in res.errors:
        print(f"  !  MCP server skipped (non-conformant): {err}", file=sys.stderr)
MEMORY_INDEX_REL_PATH = "references/memory-index.json"
MEMORY_INDEX_EXTRA_DOC_NAMES = ("CHANGELOG.md", "README.md", "build-team-plan.md")
#: Directory names that are scratch, cache or snapshot — never durable sources.
#:
#: The memory index had no exclusion of any kind, so every recursive ``*.md`` scan
#: swept them in. With ``memory_index_extra_dirs: ["examples"]`` declared, that
#: pulled in ``examples/*/expected/.agentteams-backups/**`` — **1488 backup snapshot
#: files, 83% of a 2120-document index**, and grew the committed artifact to 51 MB
#: while ``_memory_index_sources`` documented the opposite.
#:
#: Backup snapshots are near-duplicates of the canonical documents beside them, so
#: they do not merely bloat the index: they dilute BM25 scoring and let a query
#: return an old copy of a document instead of the document.
#:
#: **This is the fifth copy of this vocabulary in the package** — ``backup.py``
#: (`_BACKUP_DIR_NAME`), ``fleet.py``, ``baseline.py`` (`_DEFAULT_EXCLUDE_NAMES`) and
#: an inline check in ``audit.py`` all encode a version of it. That duplication is a
#: CH-05 defect in its own right; consolidating it touches fleet and baseline
#: behaviour and is logged rather than done here.
_SCRATCH_DIR_NAMES: frozenset[str] = frozenset({
    _BACKUP_DIR_NAME,
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".venv",
    "venv",
    "node_modules",
})


def _is_durable_source(path: Path) -> bool:
    """True when no component of *path* is a scratch/cache/snapshot directory.

    Args:
        path: Candidate source file.

    Returns:
        Whether the file is eligible for indexing.
    """
    return not (_SCRATCH_DIR_NAMES & set(path.parts))


def _durable(paths: Iterable[Path]) -> list[Path]:
    """Filter *paths* to durable sources, preserving order."""
    return [p for p in paths if _is_durable_source(p)]


def _memory_index_root(manifest: dict, output_dir: Path) -> Path:
    """Resolve the project root the memory index is built against.

    Extracted from :func:`_memory_index_sources`, which already derived it, so the
    build, staleness and incremental-update paths cannot disagree about what the
    index's relative paths are relative *to*. Three call sites computing this
    independently is how a stored relative path ends up resolving nowhere.

    Args:
        manifest: Team manifest; ``existing_project_path`` wins when present.
        output_dir: The agents directory (``<project>/.github/agents`` or
            ``<project>/.claude/agents`` in the standard layout).

    Returns:
        The project root.
    """
    epp = manifest.get("existing_project_path")
    return Path(epp) if epp else output_dir.parent.parent


def _memory_index_sources(manifest: dict, output_dir: Path) -> list[Path]:
    """Collect durable text sources for the memory index (F8).

    RSR1-aware: durable, project-local sources only. Scratch, cache and snapshot
    directories are excluded by name (``_SCRATCH_DIR_NAMES``) from **every**
    recursive scan, including consumer-declared ``memory_index_extra_dirs``.

    Note what this rule is *not*: it is not "exclude gitignored paths".
    ``workSummaries/`` and ``references/plans/`` are gitignored here yet are the
    durable history this index exists to serve — gitignore marks "local", not
    "disposable", and conflating the two would gut the feature. The rule is
    therefore about *scratch*, identified by directory name. Prefers the manifest's ``existing_project_path`` (the
    operator's explicit signal of the project root, e.g. when ``--output``
    is non-standard); falls back to inferring from ``output_dir`` when
    absent (standard layout: ``<project>/.github/agents`` or
    ``<project>/.claude/agents``).
    """
    project_root = _memory_index_root(manifest, output_dir)
    sources: list[Path] = []
    # Work summaries (the canonical durable history substrate).
    ws = project_root / "workSummaries"
    if ws.exists() and ws.is_dir():
        sources.extend(_durable(sorted(ws.rglob("*.md"))))
    # Top-level durable docs.
    for name in MEMORY_INDEX_EXTRA_DOC_NAMES:
        p = project_root / name
        if p.exists() and p.is_file():
            sources.append(p)
    # Additional durable authored docs.
    docs_src = project_root / "docs_src"
    if docs_src.exists() and docs_src.is_dir():
        sources.extend(_durable(sorted(docs_src.glob("*.md"))))
    refs = project_root / "references"
    if refs.exists() and refs.is_dir():
        sources.extend(_durable(sorted(refs.rglob("*.md"))))
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
                    candidates = _durable(sorted(project_root.glob(raw)))
                else:
                    target = (project_root / raw)
                    if not (target.exists() and target.is_dir()):
                        continue
                    try:
                        target.resolve().relative_to(project_root_resolved)
                    except (ValueError, OSError):
                        continue
                    candidates = _durable(sorted(target.rglob("*.md")))
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
    if is_index_stale(index, sources, root=_memory_index_root(manifest, output_dir)):
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
            f"  {idx}. score={hit['score']:.6f} confidence={hit['confidence']}  {hit['title']}\n"
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
                root=_memory_index_root(manifest, output_dir),
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
        root=_memory_index_root(manifest, output_dir),
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



# Code & API index artifacts carved to cli/code_index_artifacts.py (CH-07 length ceiling).
# Re-exported so cli/generate.py, git_hooks.py, build_team and tests resolve them here unchanged.
from agentteams.cli.code_index_artifacts import (  # noqa: E402,F401
    CODE_INDEX_REL_DIR,
    CODE_INDEX_VCACHE_REL_PATH,
    _code_index_project_root,
    _code_index_schema_bytes,
    _code_index_sources,
    _extra_dir_candidates,
    _load_json_or_none,
    _read_code_index,
    _refresh_existing_code_index,
    _resolve_or_self,
    _run_query_code_index,
    _run_refresh_code_index,
    _validate_code_index_schema,
    _within_root,
    _write_code_index,
    _write_code_index_partition,
)


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


