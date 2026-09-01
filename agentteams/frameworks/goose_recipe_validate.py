"""Structural validation of Goose recipe YAML (CH-07 carve out of ``goose.py``).

Regex-only structural checker for emitted/authored Goose recipes — the codebase intentionally
avoids a YAML dependency. Carved from ``goose.py`` (CH-07 module-size ceiling) alongside the
existing read-side carve ``goose_recipe_read.py``; ``goose.py`` re-imports
:func:`_validate_recipe_yaml` so every ``from agentteams.frameworks.goose import
_validate_recipe_yaml`` call site (bridge, recipe_check, the recipe test suite) keeps working.

Pure and stdlib-only; imports only the shared ``_RECIPE_SUB_PATH_RE`` from ``goose_recipe_read``
(a leaf module), so no import cycle with ``goose.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

# ``_RECIPE_SUB_PATH_RE`` lives in the read-side carve and is shared with the sub-recipe
# existence check below.
from agentteams.frameworks.goose_recipe_read import _RECIPE_SUB_PATH_RE

# Regex patterns for structural recipe validation (_validate_recipe_yaml).
_RECIPE_VERSION_RE = re.compile(r'^version:\s*"1\.0\.0"', re.MULTILINE)
_RECIPE_MODEL_KEY_RE = re.compile(r"^\s*model:", re.MULTILINE)
_RECIPE_TITLE_RE = re.compile(r'^title:\s*".+?"', re.MULTILINE)
_RECIPE_INSTRUCTIONS_RE = re.compile(r"^instructions:\s*\|", re.MULTILINE)
# Phase-4a: a `parameters:` block (when present) must list `- key:` entries.
_RECIPE_PARAMETERS_RE = re.compile(r"^parameters:\s*$", re.MULTILINE)
_RECIPE_PARAM_KEY_RE = re.compile(r'^\s+-\s+key:\s*"', re.MULTILINE)
# Phase-4b: a `response:` block (when present) must carry a non-empty `json_schema:`.
_RECIPE_RESPONSE_RE = re.compile(r"^response:\s*$", re.MULTILINE)
_RECIPE_JSON_SCHEMA_RE = re.compile(r"^\s+json_schema:\s*\S", re.MULTILINE)
# Phase-4c: a `retry:` block (when present) must carry `max_retries:` and ≥1 check `command:`.
_RECIPE_RETRY_RE = re.compile(r"^retry:\s*$", re.MULTILINE)
_RECIPE_MAX_RETRIES_RE = re.compile(r"^\s+max_retries:\s*\d", re.MULTILINE)
_RECIPE_RETRY_CMD_RE = re.compile(r'^\s+command:\s*"', re.MULTILINE)

# Forbidden-shape guards for emitted recipes (goose-integration.plan §6.5 gotchas).
# These have no other validator backing, so a typo would otherwise pass silently.
# The optional ``-\s*`` prefix matches a key that is the first entry of a list item
# (``  - type: sse``); ``\b`` after ``sse`` avoids matching a uri ending in ``/sse``.
_RECIPE_FORBIDDEN_ENVS_RE = re.compile(r"^\s*(-\s*)?envs:", re.MULTILINE)        # use env_keys
_RECIPE_FORBIDDEN_SSE_RE = re.compile(r'^\s*(-\s*)?type:\s*["\']?sse\b', re.MULTILINE)  # use streamable_http
_RECIPE_FORBIDDEN_CONTEXT_RE = re.compile(r"^\s*context:", re.MULTILINE)     # not a recipe field


def _validate_recipe_yaml(yaml_text: str, recipes_dir: Path | None = None) -> list[str]:
    """Return structural violations found in a Goose recipe YAML string.

    Uses regex-only parsing — the codebase intentionally avoids a YAML dependency.
    Pass ``recipes_dir`` to also resolve ``sub_recipes`` path references on disk.
    """
    violations: list[str] = []
    if not _RECIPE_VERSION_RE.search(yaml_text):
        violations.append('missing or wrong version: field (expected version: "1.0.0")')
    if _RECIPE_MODEL_KEY_RE.search(yaml_text):
        violations.append("forbidden model: key (Goose infers model from session config)")
    if not _RECIPE_TITLE_RE.search(yaml_text):
        violations.append("missing or empty title: field")
    if not _RECIPE_INSTRUCTIONS_RE.search(yaml_text):
        violations.append("missing instructions: literal block scalar (instructions: |)")
    if _RECIPE_FORBIDDEN_ENVS_RE.search(yaml_text):
        violations.append("forbidden envs: key (recipe extensions use env_keys, not envs)")
    if _RECIPE_FORBIDDEN_SSE_RE.search(yaml_text):
        violations.append("forbidden type: sse (use streamable_http; sse is deprecated)")
    if _RECIPE_FORBIDDEN_CONTEXT_RE.search(yaml_text):
        violations.append("forbidden context: field (not a recipe field)")
    if _RECIPE_PARAMETERS_RE.search(yaml_text) and not _RECIPE_PARAM_KEY_RE.search(yaml_text):
        violations.append("parameters: block present but lists no '- key:' entries")
    if _RECIPE_RESPONSE_RE.search(yaml_text) and not _RECIPE_JSON_SCHEMA_RE.search(yaml_text):
        violations.append("response: block present but has no non-empty json_schema: value")
    if _RECIPE_RETRY_RE.search(yaml_text) and not (
        _RECIPE_MAX_RETRIES_RE.search(yaml_text) and _RECIPE_RETRY_CMD_RE.search(yaml_text)
    ):
        violations.append("retry: block present but missing max_retries: or a check command:")
    if recipes_dir is not None:
        for path_val in _RECIPE_SUB_PATH_RE.findall(yaml_text):
            resolved = (recipes_dir / path_val).resolve()
            if not resolved.exists():
                violations.append(f"sub_recipe path not found: {path_val}")
    return violations
