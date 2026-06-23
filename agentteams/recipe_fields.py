"""Normalization for opt-in Goose recipe fields (Phase-4 goose-native).

Pure, dependency-free normalizers for the operator-declared brief fields that map
to Goose recipe blocks — ``recipe_parameters`` (4a), ``recipe_response`` (4b), and
``recipe_retry`` (4c). ``analyze.build_manifest`` calls these and copies a result
into the team-manifest only when truthy, so manifests for briefs that declare none
are byte-identical. Kept out of ``analyze.py`` to hold that module under the CH-07
line ceiling; re-exported from ``analyze`` for backward compatibility.
"""

from __future__ import annotations

from typing import Any


def _normalize_recipe_response(raw: Any) -> dict[str, Any] | None:
    """Normalize a brief's ``recipe_response`` into a Goose recipe response schema.

    Phase-4b goose-native (opt-in). Goose stores ``response.json_schema`` as a raw
    value with no load-time validity check, so the only guard against a silently
    useless block is here. Returns ``None`` (→ no ``response:`` emitted) unless
    ``raw`` is a non-empty dict carrying a non-empty string ``type``. Tolerates the
    common double-wrap ``{"json_schema": {...}}`` by unwrapping when the top level
    has no ``type``. Otherwise lenient — goose validates the schema's internals.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    schema = raw
    if "type" not in schema and isinstance(schema.get("json_schema"), dict):
        schema = schema["json_schema"]  # tolerate {"json_schema": {...}} double-wrap
    if not isinstance(schema.get("type"), str) or not schema["type"]:
        return None
    return schema


_RETRY_MAX_CAP = 10
_RETRY_DEFAULT_MAX = 3
_RETRY_DEFAULT_TIMEOUT = 300
_RETRY_TIMEOUT_CAP = 3600


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    """Coerce ``value`` to int and clamp to [lo, hi]; ``default`` on non-numeric."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _normalize_recipe_retry(raw: Any) -> dict[str, Any] | None:
    """Normalize a brief's ``recipe_retry`` into a bounded Goose recipe retry block.

    Phase-4c goose-native (opt-in). Goose's ``retry`` re-runs the recipe until the
    shell ``checks`` pass. ``checks`` is a Goose-REQUIRED field, so a retry with no
    valid check is an invalid recipe → returns ``None`` (no ``retry:`` emitted).
    Safety (design §6, "never emit an unbounded retry"): ``max_retries`` is clamped to
    [1, _RETRY_MAX_CAP] (default 3); a bounded ``timeout_seconds`` is ALWAYS emitted,
    clamped to [1, _RETRY_TIMEOUT_CAP] (default 300). ``checks[].command`` /
    ``on_failure`` are the operator's own shell commands (like a CI step), emitted
    YAML-quoted; no sanitization beyond quoting (they are meant to be shell).
    """
    if not isinstance(raw, dict):
        return None
    checks: list[dict[str, str]] = []
    for check in raw.get("checks") or []:
        if not isinstance(check, dict):
            continue
        command = check.get("command")
        if isinstance(command, str) and command.strip():
            checks.append({"type": "shell", "command": command.strip()})
    if not checks:
        return None  # checks is goose-required; a check-less retry is invalid
    retry: dict[str, Any] = {
        "max_retries": _clamp_int(raw.get("max_retries"), _RETRY_DEFAULT_MAX, 1, _RETRY_MAX_CAP),
        "timeout_seconds": _clamp_int(
            raw.get("timeout_seconds"), _RETRY_DEFAULT_TIMEOUT, 1, _RETRY_TIMEOUT_CAP
        ),
        "checks": checks,
    }
    on_failure_timeout = _clamp_int(raw.get("on_failure_timeout_seconds"), 0, 0, _RETRY_TIMEOUT_CAP)
    if on_failure_timeout > 0:
        retry["on_failure_timeout_seconds"] = on_failure_timeout
    on_failure = raw.get("on_failure")
    if isinstance(on_failure, str) and on_failure.strip():
        retry["on_failure"] = on_failure.strip()
    return retry


_RECIPE_PARAM_INPUT_TYPES = frozenset(
    {"string", "number", "boolean", "date", "file", "select"}
)


def _normalize_recipe_parameters(raw: Any) -> list[dict[str, str]]:
    """Normalize a brief's ``recipe_parameters`` into validated Goose recipe params.

    Phase-4a goose-native (opt-in). Drops malformed entries (missing/blank string
    ``key``); defaults ``input_type=string`` and ``requirement=optional``. Enforces
    two Goose hard rules: optional non-file params MUST carry a ``default`` ("" when
    unset), and ``file`` params cannot have a default (so they are coerced to
    ``required``). Returns ``[]`` when ``raw`` is absent or not a list, so manifests
    for briefs that declare none are unchanged.
    """
    if not isinstance(raw, list):
        return []
    params: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            continue
        input_type = item.get("input_type")
        if input_type not in _RECIPE_PARAM_INPUT_TYPES:
            input_type = "string"
        requirement = item.get("requirement")
        if requirement not in ("required", "optional"):
            requirement = "optional"
        param: dict[str, str] = {
            "key": key.strip(),
            "input_type": input_type,
            "requirement": requirement,
        }
        description = item.get("description")
        if isinstance(description, str) and description.strip():
            param["description"] = description.strip()
        default = item.get("default")
        if input_type == "file":
            # Goose forbids defaults on file params; optional+file is contradictory.
            param["requirement"] = "required"
        elif default is not None:
            if isinstance(default, bool):
                param["default"] = "true" if default else "false"
            elif isinstance(default, str):
                param["default"] = default
            else:
                param["default"] = str(default)
        elif requirement == "optional":
            # Goose requires optional params to declare a default.
            param["default"] = ""
        params.append(param)
    return params
