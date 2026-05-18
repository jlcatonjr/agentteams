"""Typed handoff payload substrate (Cluster C, plan rev3).

Provides:
- ``load_payload_schema`` — path-restricted loader (V1)
- ``strip_llm_visible_text`` — mechanical sanitizer of LLM-visible schema fields (V3)
- ``_assert_bounded_schema`` — depth + cycle pre-walk (V5)
- ``validate`` — depth-bounded, timeout-bounded validation (V5)
- ``audit_handoff_chain`` — $id-only comparator for adjacent handoff steps (V4, V6)
- ``PAYLOAD_UNTYPED_HARD_DATE`` — dated promotion cutoff (V6)
"""

from __future__ import annotations

import json
import multiprocessing
import re
import warnings
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

PAYLOAD_UNTYPED_HARD_DATE = date(2026, 7, 1)
MAX_DEPTH = 32
VALIDATE_TIMEOUT_SECONDS = 2.0

_PAYLOAD_GLOB_RE = re.compile(r"^schemas/handoff-payloads/[a-z0-9-]+\.v[0-9]+\.schema\.json$")
_LLM_VISIBLE_KEYS = ("description", "title", "$comment", "examples")


class PayloadSchemaError(ValueError):
    """Raised when a payload_schema reference is rejected by the loader."""


class SchemaInvalid(RuntimeError):
    """Raised when a payload schema fails depth, cycle, or timeout limits."""


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str


def load_payload_schema(value: str, repo_root: Path) -> dict[str, Any]:
    """Load a payload schema by relative repo path. Rejects traversal/URLs (V1)."""
    if not isinstance(value, str) or not value:
        raise PayloadSchemaError("payload_schema must be a non-empty string")
    if "://" in value or value.startswith(("/", "\\")):
        raise PayloadSchemaError(f"payload_schema must be a relative repo path, not URL or absolute: {value!r}")
    if ".." in Path(value).parts:
        raise PayloadSchemaError(f"payload_schema must not contain '..' segments: {value!r}")
    if not _PAYLOAD_GLOB_RE.match(value):
        raise PayloadSchemaError(
            f"payload_schema must match schemas/handoff-payloads/<slug>.v<n>.schema.json: {value!r}"
        )
    target = (repo_root / value).resolve()
    root_resolved = repo_root.resolve()
    if root_resolved not in target.parents and target != root_resolved:
        raise PayloadSchemaError(f"payload_schema escapes repo root: {value!r}")
    return json.loads(target.read_text(encoding="utf-8"))


def strip_llm_visible_text(schema: Any) -> Any:
    """Recursively remove description/title/$comment/examples (V3)."""
    if isinstance(schema, dict):
        return {k: strip_llm_visible_text(v) for k, v in schema.items() if k not in _LLM_VISIBLE_KEYS}
    if isinstance(schema, list):
        return [strip_llm_visible_text(item) for item in schema]
    return schema


def _assert_bounded_schema(schema: Any, *, max_depth: int = MAX_DEPTH) -> None:
    """Reject schemas exceeding ``max_depth`` or containing self-referential ``$ref`` cycles (V5)."""
    seen_ids: set[int] = set()

    def walk(node: Any, depth: int) -> None:
        if depth > max_depth:
            raise SchemaInvalid(f"schema depth exceeds {max_depth}")
        if isinstance(node, dict):
            ident = id(node)
            if ident in seen_ids:
                raise SchemaInvalid("schema contains a cyclic $ref or self-referential node")
            seen_ids.add(ident)
            try:
                for value in node.values():
                    walk(value, depth + 1)
            finally:
                seen_ids.discard(ident)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(schema, 0)


def _validation_mp_context() -> "multiprocessing.context.BaseContext":
    """Pick a start method that does not re-execute the caller's ``__main__``.

    ``spawn`` (macOS/Windows default) and ``forkserver`` both re-import the
    caller's ``__main__`` to bootstrap the child/server; a caller that invokes
    ``validate`` from an unguarded top-level script then recursively re-runs
    and the worker dies before producing a result — surfacing as a misleading
    "schema invalid". Only ``fork`` avoids the re-import. Bare ``fork`` of a
    multi-threaded process is deprecated (3.12+) because inherited locks can
    deadlock the child, but ``_validate_worker`` acquires no inherited lock,
    does pure-CPU work, and exits immediately, so that hazard does not apply
    here — the specific DeprecationWarning is suppressed at the start site with
    this justification. Prefer ``fork``; fall back to ``spawn`` only on
    platforms without it (Windows), where the ``__main__``-guard requirement is
    unavoidable and standard. The process stays ``terminate``-able, preserving
    the V5 timeout guard.
    """
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("fork" if "fork" in methods else "spawn")


def _validate_worker(payload: Any, schema: dict[str, Any], queue: "multiprocessing.Queue") -> None:
    try:
        import jsonschema  # imported in worker so import failure surfaces as SchemaInvalid
        jsonschema.validate(payload, schema)
        queue.put(("ok", None))
    except Exception as exc:  # noqa: BLE001
        queue.put(("err", f"{type(exc).__name__}: {exc}"))


def validate(
    payload: Any,
    schema: dict[str, Any],
    *,
    timeout: float = VALIDATE_TIMEOUT_SECONDS,
    _worker: Callable[..., None] | None = None,
) -> None:
    """Validate payload against schema with depth + wallclock guards (V5).

    ``_worker`` is an injection seam for tests; production callers must omit it.
    """
    _assert_bounded_schema(schema)
    ctx = _validation_mp_context()
    queue: multiprocessing.Queue = ctx.Queue()
    target = _worker if _worker is not None else _validate_worker
    proc = ctx.Process(target=target, args=(payload, schema, queue))
    with warnings.catch_warnings():
        # See _validation_mp_context: fork-of-multithreaded is safe for this
        # lock-free, short-lived worker; suppress only that specific advisory.
        warnings.filterwarnings(
            "ignore",
            message=r".*fork.*may lead to deadlocks in the child.*",
            category=DeprecationWarning,
        )
        proc.start()
    proc.join(timeout)
    if proc.is_alive():
        proc.terminate()
        proc.join(1.0)
        raise SchemaInvalid(f"validation exceeded {timeout}s")
    if queue.empty():
        raise SchemaInvalid(
            "validation worker exited without result "
            f"(exitcode={proc.exitcode}); this is a validation-infrastructure "
            "failure, not necessarily an invalid schema"
        )
    status, detail = queue.get()
    if status == "err":
        raise SchemaInvalid(detail)


def _payload_untyped_severity(today: date | None = None) -> str:
    today = today or date.today()
    return "HARD" if today >= PAYLOAD_UNTYPED_HARD_DATE else "WARN"


def audit_handoff_chain(
    steps: list[dict[str, str]],
    *,
    today: date | None = None,
) -> list[Finding]:
    """Compare ``payload_schema_out`` of step N with ``payload_schema_in`` of step N+1.

    Compares $id strings only (V3, V4). PAYLOAD_UNTYPED severity is dated (V6).
    """
    findings: list[Finding] = []
    untyped_severity = _payload_untyped_severity(today)
    for i in range(len(steps) - 1):
        out_id = (steps[i].get("payload_schema_out") or "").strip()
        in_id = (steps[i + 1].get("payload_schema_in") or "").strip()
        if not out_id or not in_id:
            findings.append(
                Finding(
                    "PAYLOAD_UNTYPED",
                    untyped_severity,
                    f"step {i + 1}->step {i + 2}: missing payload_schema_out/in",
                )
            )
            continue
        if out_id != in_id:
            findings.append(
                Finding(
                    "PAYLOAD_MISMATCH",
                    "HARD",
                    f"step {i + 1}->step {i + 2}: $id mismatch ({out_id!r} != {in_id!r})",
                )
            )
    return findings


__all__ = [
    "Finding",
    "MAX_DEPTH",
    "PAYLOAD_UNTYPED_HARD_DATE",
    "PayloadSchemaError",
    "SchemaInvalid",
    "VALIDATE_TIMEOUT_SECONDS",
    "audit_handoff_chain",
    "load_payload_schema",
    "strip_llm_visible_text",
    "validate",
]
