"""Normalization for opt-in Goose recipe fields (Phase-4 goose-native).

Pure, dependency-free normalizers for the operator-declared brief fields that map
to Goose recipe blocks — ``recipe_parameters`` (4a) and ``recipe_response`` (4b).
``analyze.build_manifest`` calls these and copies a result into the team-manifest
only when truthy, so manifests for briefs that declare none are byte-identical.
Kept out of ``analyze.py`` to hold that module under the CH-07 line ceiling;
re-exported from ``analyze`` for backward compatibility.
"""

from __future__ import annotations

from typing import Any

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
