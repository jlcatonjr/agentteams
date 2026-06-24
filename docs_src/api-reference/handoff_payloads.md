# `handoff_payloads` — AgentTeamsModule

Typed handoff payload substrate for plan `.steps.csv` artifacts.

Each row of a plan `.steps.csv` describes a handoff from one agent to the next. When a row declares `payload_schema_out` (and the next row declares `payload_schema_in`), the handoff is **typed**: the payload is validated against a JSON Schema and the `$id` strings of adjacent steps are compared. This module provides the loader, sanitizer, validator, and chain comparator that implement that contract.

Designed against six vulnerability classes (V1-V6): path traversal in schema references, schema poisoning via permissive payloads, LLM injection via schema text, `$id` version drift across handoffs, DoS via recursive or slow schemas, and opt-in bypass through indefinite WARN-only enforcement.

> *Source: `agentteams/handoff_payloads.py`*

---

## Constants

- `PAYLOAD_UNTYPED_HARD_DATE` (`datetime.date`) — `date(2026, 7, 1)`. On or after this date, `PAYLOAD_UNTYPED` findings are emitted at HARD severity instead of WARN. Enforced mechanically; no editorial override.
- `MAX_DEPTH` (`int`) — `32`. Maximum allowed nesting depth for any payload schema. Schemas exceeding this raise `SchemaInvalid`.
- `VALIDATE_TIMEOUT_SECONDS` (`float`) — `2.0`. Default wallclock timeout for the multiprocessing-isolated validator.

---

## Exceptions

### `PayloadSchemaError`

> *Source: `agentteams/handoff_payloads.py`*

Subclass of `ValueError`. Raised by `load_payload_schema` when a `payload_schema` reference is rejected (URL, absolute path, `..` segment, non-matching glob, or repo-root escape).

### `SchemaInvalid`

> *Source: `agentteams/handoff_payloads.py`*

Subclass of `RuntimeError`. Raised by `_assert_bounded_schema` (depth/cycle violation) or by `validate` (worker error, timeout, or empty worker result).

---

## Dataclasses

### `Finding`

> *Source: `agentteams/handoff_payloads.py`*

Frozen dataclass describing a single conflict-auditor finding produced by `audit_handoff_chain`.

**Attributes:**

- `code` (`str`) — `'PAYLOAD_UNTYPED'` or `'PAYLOAD_MISMATCH'`.
- `severity` (`str`) — `'WARN'` or `'HARD'`. For `PAYLOAD_UNTYPED`, severity is `'WARN'` before `PAYLOAD_UNTYPED_HARD_DATE` and `'HARD'` on/after.
- `message` (`str`) — Human-readable description identifying the offending step pair.

---

## Functions

### `load_payload_schema(value, repo_root)`

> *Source: `agentteams/handoff_payloads.py`*

Load a payload schema by relative repo path. Rejects URLs, absolute paths, `..` segments, paths outside `schemas/handoff-payloads/<slug>.v<n>.schema.json`, uppercase slugs, and paths that resolve outside `repo_root` (V1 mitigation).

**Args:**

- `value` (`str`) — Relative path string from the `payload_schema_in` / `payload_schema_out` CSV cell.
- `repo_root` (`Path`) — Repository root used as the base for resolution and the boundary for escape detection.

**Returns:** `dict[str, Any]` — Parsed JSON schema.

**Raises:** `PayloadSchemaError` if the reference is rejected for any reason.

---

### `strip_llm_visible_text(schema)`

> *Source: `agentteams/handoff_payloads.py`*

Recursively remove the keys `description`, `title`, `$comment`, and `examples` from a schema (V3 mitigation). Use before exposing a schema to any LLM-visible surface. Structural keys (`type`, `properties`, `required`, `$id`, etc.) are preserved.

**Args:**

- `schema` (`Any`) — Schema dict, list, or scalar.

**Returns:** `Any` — A new structure with the same shape minus the four stripped keys.

---

### `validate(payload, schema, *, timeout=VALIDATE_TIMEOUT_SECONDS, _worker=None)`

> *Source: `agentteams/handoff_payloads.py`*

Validate `payload` against `schema` with depth, cycle, and wallclock bounds (V5 mitigation).

The function first runs `_assert_bounded_schema` to reject schemas deeper than `MAX_DEPTH` or containing self-referential cycles. It then validates inside a subprocess from `multiprocessing.get_context("fork" if "fork" in available_start_methods else "spawn")` — `fork` is preferred (it avoids re-importing `__main__`), falling back to `spawn` only on platforms without `fork` (e.g. Windows) — so a slow or malicious schema cannot block the parent. If the subprocess exceeds `timeout` seconds, it is terminated and `SchemaInvalid` is raised.

**Args:**

- `payload` (`Any`) — The payload object to validate.
- `schema` (`dict[str, Any]`) — A loaded payload schema.
- `timeout` (`float`, keyword-only) — Wallclock seconds before the worker is terminated. Default: `2.0`.
- `_worker` (`Callable | None`, keyword-only) — Test seam for injecting an alternate worker callable. Production callers must omit this argument.

**Returns:** `None` on success.

**Raises:** `SchemaInvalid` for depth/cycle violations, validator errors, timeout, or empty worker results.

> **Note — Worker callable pickling (spawn fallback only).** This caveat applies only when the `spawn` fallback is used (platforms without `fork`, e.g. Windows): `spawn` pickles the worker callable, so custom workers (used only by tests) must be top-level module functions, not closures or local functions. Under the preferred `fork` context the child inherits the callable and no pickling occurs, but tests should still use top-level functions for portability. The default worker is `_validate_worker` inside this module.

---

### `audit_handoff_chain(steps, *, today=None)`

> *Source: `agentteams/handoff_payloads.py`*

Compare `payload_schema_out` of step *N* with `payload_schema_in` of step *N+1* for adjacent rows.

Comparison is `$id`-string only — schema bodies are never compared (V4 mitigation, and avoids re-introducing the LLM-injection surface stripped by `strip_llm_visible_text`). Findings:

- `PAYLOAD_UNTYPED` — at least one of the two cells is empty. Severity is `WARN` before `PAYLOAD_UNTYPED_HARD_DATE` and `HARD` on/after (V6 mitigation).
- `PAYLOAD_MISMATCH` — both cells are populated but the `$id` strings differ. Severity is always `HARD`.

**Args:**

- `steps` (`list[dict[str, str]]`) — Rows produced by `agentteams.plan_steps.read_steps`.
- `today` (`date | None`, keyword-only) — Date used to evaluate the `PAYLOAD_UNTYPED` severity gate. Defaults to `date.today()`. Tests pass an explicit value to exercise the cutoff.

**Returns:** `list[Finding]` — One finding per offending adjacent pair; an empty list means the chain is fully typed and consistent.

---

## Internal Symbols

`_assert_bounded_schema`, `_validate_worker`, and `_payload_untyped_severity` are internal implementation details (depth pre-walk, default validator worker, and dated severity gate respectively). They are not part of the supported public API and may change without notice.

---

## See Also

- [`plan_steps`](plan_steps.md) — reader for the `.steps.csv` artifacts whose rows declare `payload_schema_in` / `payload_schema_out`.
- `schemas/handoff-payload-meta.schema.json` — meta-schema enforcing structural constraints on every concrete payload schema (V2 mitigation).
- `schemas/handoff-payloads/conflict-audit-result.v1.schema.json` — worked-example payload schema for `@conflict-auditor` findings.
- `agentteams/templates/universal/conflict-auditor.template.md` — fenced `handoff_payload_codes` section declaring the `PAYLOAD_UNTYPED` / `PAYLOAD_MISMATCH` conflict codes.
