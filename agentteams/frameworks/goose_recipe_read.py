"""
goose_recipe_read.py — READ-side parse of Goose recipe YAML (carved from
goose.py under the CH-07 line ceiling, durable-canonical-agent-format plan
steps F.1/F.3).

Export-side extraction of a recipe's CAI fields (title/description/
instructions block scalar, builtin extension names, sub_recipe slugs, load()
references, and the Phase-4 parameters/response/retry configuration blocks).
Regex parse, no YAML dep — the codebase convention for recipes (see
goose.py::_validate_recipe_yaml and bridge_sources._parse_recipe_meta).

Import graph: goose.py imports this module (mirrors the goose_docs.py carve);
this module never imports goose.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Shared with goose.py::_validate_recipe_yaml (defined here, re-imported
# there, so the carve stays cycle-free).
_RECIPE_SUB_PATH_RE = re.compile(r'path:\s*"([^"]+)"')



# ---------------------------------------------------------------------------
# Recipe READ helpers (F.1): export-side parse of a recipe YAML into CAI
# fields. Regex parse, no YAML dep — same convention as _validate_recipe_yaml
# and bridge_sources._parse_recipe_meta.
# ---------------------------------------------------------------------------

# Tolerant variants of the validation regexes (quoted OR bare scalars).
_RECIPE_TITLE_ANY_RE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)
_RECIPE_DESC_ANY_RE = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)
# `name:` entries inside the extensions: block (builtin extension names and
# MCP server ids alike — the coarse canonical map ignores what it doesn't know).
_RECIPE_EXT_NAME_RE = re.compile(r"^\s+name:\s*[\"']?([\w.-]+)[\"']?\s*$", re.MULTILINE)
# summon load("<slug>") context-load references inside instructions.
_RECIPE_LOAD_RE = re.compile(r"load\(\"([A-Za-z0-9._-]+)\"\)")


def _recipe_block_scalar(yaml_text: str, key: str) -> str:
    """Extract a literal block scalar (``key: |``) value, dedented (F.1).

    Recipes are hand-built with uniform indentation (see ``_emit_recipe``):
    the block starts at ``key: |`` and continues while lines are blank or
    indented; the first non-blank column-0 line ends it. The indent of the
    first non-blank content line is the block indent.
    """
    out: list[str] = []
    in_block = False
    block_indent: int | None = None
    for line in yaml_text.splitlines():
        if not in_block:
            if re.match(rf"^{re.escape(key)}:\s*\|", line):
                in_block = True
            continue
        if line.strip() == "":
            out.append("")
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break
        if block_indent is None:
            block_indent = indent
        out.append(line[block_indent:] if len(line) >= block_indent else line.lstrip())
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n" if out else ""


def _recipe_section(yaml_text: str, key: str) -> str:
    """Raw text of a top-level block: from ``key:`` to the next column-0 key."""
    out: list[str] = []
    in_section = False
    for line in yaml_text.splitlines():
        if not in_section:
            if re.match(rf"^{re.escape(key)}:\s*$", line):
                in_section = True
            continue
        if line.strip() and not line.startswith((" ", "\t")):
            break
        out.append(line)
    return "\n".join(out)


def parse_recipe_fields(yaml_text: str) -> dict[str, Any]:
    """Parse one goose recipe YAML into CAI-export fields (F.1/F.3).

    Returns ``title``, ``description``, ``instructions`` (the recipe body),
    ``extension_names`` (builtin + MCP names from the extensions block),
    ``builtin_extension_names`` (``type: builtin`` only — what the
    framework_extensions.goose bucket carries), ``sub_recipe_slugs``
    (true-delegation targets, from sub_recipes paths), ``load_refs``
    (context-load references inside instructions), and the recipe-level
    configuration blocks ``parameters`` / ``response`` / ``retry`` (F.3).
    """
    title_m = _RECIPE_TITLE_ANY_RE.search(yaml_text)
    desc_m = _RECIPE_DESC_ANY_RE.search(yaml_text)
    instructions = _recipe_block_scalar(yaml_text, "instructions")
    sub_slugs = [
        Path(path_val).stem
        for path_val in _RECIPE_SUB_PATH_RE.findall(_recipe_section(yaml_text, "sub_recipes"))
    ]
    ext_section = _recipe_section(yaml_text, "extensions")
    ext_names = _RECIPE_EXT_NAME_RE.findall(ext_section)
    builtin_names = _builtin_extension_names(ext_section)
    return {
        "title": title_m.group(1).strip().strip('"') if title_m else "",
        "description": desc_m.group(1).strip().strip('"') if desc_m else "",
        "instructions": instructions,
        "extension_names": ext_names,
        "builtin_extension_names": builtin_names,
        "sub_recipe_slugs": sub_slugs,
        "load_refs": _RECIPE_LOAD_RE.findall(instructions),
        "parameters": _recipe_parameters(yaml_text),
        "response": _recipe_response(yaml_text),
        "retry": _recipe_retry(yaml_text),
    }


def _yaml_unquote(value: str) -> str:
    """Reverse ``_yaml_dq`` for a double-quoted scalar (backslash escapes)."""
    v = value.strip()
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        v = v[1:-1]
    return v.replace('\\"', '"').replace("\\\\", "\\")


def _builtin_extension_names(ext_section: str) -> list[str]:
    """Names of ``type: builtin`` extension items only (F.3).

    Platform items (summon) are re-derived from handoffs at render time and
    MCP items (stdio/streamable_http) are mcp-server concerns — neither
    belongs in the framework_extensions.goose bucket.
    """
    names: list[str] = []
    current_type = ""
    for line in ext_section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- type:"):
            current_type = _yaml_unquote(stripped.split(":", 1)[1]).strip().lower()
        elif stripped.startswith("name:") and current_type == "builtin":
            name = _yaml_unquote(stripped.split(":", 1)[1]).strip()
            if name:
                names.append(name)
    return names


def _recipe_parameters(yaml_text: str) -> list[dict[str, str]] | None:
    """Parse the ``parameters:`` block (Phase-4a emission shape) or None."""
    section = _recipe_section(yaml_text, "parameters")
    if not section.strip():
        return None
    params: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- key:"):
            if current:
                params.append(current)
            current = {"key": _yaml_unquote(stripped.split(":", 1)[1])}
        elif current is not None and ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            if key in ("input_type", "requirement", "default", "description"):
                current[key] = _yaml_unquote(value)
    if current:
        params.append(current)
    return params or None


def _recipe_response(yaml_text: str) -> dict[str, Any] | None:
    """Parse the ``response:`` block's single-line ``json_schema`` or None."""
    section = _recipe_section(yaml_text, "response")
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("json_schema:"):
            try:
                schema = json.loads(stripped.split(":", 1)[1].strip())
            except ValueError:
                return None
            return schema if isinstance(schema, dict) else None
    return None


def _recipe_retry(yaml_text: str) -> dict[str, Any] | None:
    """Parse the ``retry:`` block (Phase-4c emission shape) or None."""
    section = _recipe_section(yaml_text, "retry")
    if not section.strip():
        return None
    retry: dict[str, Any] = {}
    checks: list[dict[str, str]] = []
    current_check: dict[str, str] | None = None
    in_checks = False
    checks_indent = 0
    for line in section.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if in_checks and indent > checks_indent:
            # still inside the checks: list (items indent deeper than the key)
            if stripped.startswith("- type:"):
                if current_check:
                    checks.append(current_check)
                current_check = {"type": _yaml_unquote(stripped.split(":", 1)[1])}
            elif current_check is not None and stripped.startswith("command:"):
                current_check["command"] = _yaml_unquote(stripped.split(":", 1)[1])
            continue
        in_checks = False  # dedented back to retry-level keys
        if stripped == "checks:":
            in_checks = True
            checks_indent = indent
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key, value = key.strip(), value.strip()
            if key in ("max_retries", "timeout_seconds", "on_failure_timeout_seconds"):
                try:
                    retry[key] = int(value)
                except ValueError:
                    continue
            elif key == "on_failure":
                retry["on_failure"] = _yaml_unquote(value)
    if current_check:
        checks.append(current_check)
    if checks:
        retry["checks"] = checks
    return retry or None

