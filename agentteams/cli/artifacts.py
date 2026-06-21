"""artifacts.py — delivery-receipt / eval-suite / model-routing / memory-index writers.

Extracted verbatim from build_team.py (CH-07) except the 5 schema paths are
re-anchored from `Path(__file__).resolve().parent` (build_team at repo root)
to `parents[2]` (this module at agentteams/cli/) — the same repo-root/schemas
dir. build_team re-exports these so main and tests resolve them unchanged.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentteams.errors import (
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
def _require_jsonschema(error_cls: type[Exception], artifact: str) -> Any:
    """Import and return ``jsonschema``, or raise *error_cls* if it is absent.

    A *missing* jsonschema module is an environment gap (e.g. the run was driven
    by an interpreter without the dep), not a malformed artifact. Every artifact
    writer below is wrapped by ``main()`` in a non-fatal
    ``except (OSError, <error_cls>)`` handler, so converting the ``ImportError``
    into the writer's own error type lets a fully successful, non-destructive
    merge finish cleanly (exit 0) instead of aborting with a traceback *after*
    the merge already wrote every file. The artifact is re-emitted on the next
    ``--update`` once jsonschema is installed.
    """
    try:
        import jsonschema
        return jsonschema
    except ImportError as exc:
        raise error_cls(
            f"jsonschema is not installed; cannot validate the {artifact}. "
            "The merge itself is complete — install jsonschema (or run via the "
            f"`agentteams` console entry point) to re-emit the {artifact} on the "
            "next --update."
        ) from exc
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
    jsonschema = _require_jsonschema(DeliveryReceiptError, "delivery receipt")
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "delivery-receipt.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryReceiptError(
            f"delivery-receipt schema unavailable ({schema_path}): {exc}"
        ) from exc
    try:
        jsonschema.validate(receipt, schema)
    except jsonschema.ValidationError as exc:
        raise DeliveryReceiptError(
            f"delivery receipt failed schema validation: {exc.message}"
        ) from exc

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

    jsonschema = _require_jsonschema(EvalSuiteError, "eval suite")
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "eval-suite.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalSuiteError(
            f"eval-suite schema unavailable ({schema_path}): {exc}"
        ) from exc
    try:
        jsonschema.validate(suite, schema)
    except jsonschema.ValidationError as exc:
        raise EvalSuiteError(
            f"eval suite failed schema validation: {exc.message}"
        ) from exc

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

    jsonschema = _require_jsonschema(ModelRoutingError, "model routing contract")
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "model-routing.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelRoutingError(
            f"model-routing schema unavailable ({schema_path}): {exc}"
        ) from exc
    try:
        jsonschema.validate(contract, schema)
    except jsonschema.ValidationError as exc:
        raise ModelRoutingError(
            f"model-routing contract failed schema validation: {exc.message}"
        ) from exc

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
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryIndexError(f"failed reading memory index at {index_path}: {exc}") from exc
    _validate_memory_index_schema(index)
    return index
def _validate_memory_index_schema(index: dict[str, object]) -> None:
    """Validate a parsed memory-index payload against its schema.

    Query-mode reads must validate shape so malformed payloads fail with a
    controlled ``MemoryIndexError`` instead of surfacing raw ``KeyError`` from
    downstream ranking logic.
    """
    jsonschema = _require_jsonschema(MemoryIndexError, "memory index")

    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "memory-index.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryIndexError(
            f"memory-index schema unavailable ({schema_path}): {exc}"
        ) from exc
    try:
        jsonschema.validate(index, schema)
    except jsonschema.ValidationError as exc:
        raise MemoryIndexError(
            f"memory index failed schema validation: {exc.message}"
        ) from exc
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

    jsonschema = _require_jsonschema(MemoryIndexError, "memory index")
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "memory-index.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryIndexError(
            f"memory-index schema unavailable ({schema_path}): {exc}"
        ) from exc
    try:
        jsonschema.validate(index, schema)
    except jsonschema.ValidationError as exc:
        raise MemoryIndexError(
            f"memory index failed schema validation: {exc.message}"
        ) from exc

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index_path
