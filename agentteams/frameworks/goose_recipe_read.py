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



#: Every tool the Goose 1.37 ``developer`` builtin exposes; an unscoped ``developer`` grants all four.
DEVELOPER_TOOLS = frozenset({"write", "edit", "shell", "tree"})
#: Stands in for a value the reader cannot interpret: treated as an unscoped ``developer`` (worst case).
UNPARSED = "<unparsed>"
_EXT_KEY_RE = re.compile(r"^extensions:(.*)$", re.MULTILINE)
#: Item keys match spaces and tabs only (not ``\s``), so an empty value is not read from the following line.
_ITEM_NAME_RE = re.compile(r"^[ \t]*-?[ \t]*name:[ \t]*(.*)$", re.MULTILINE)
_ITEM_ALLOW_RE = re.compile(r"^[ \t]*available_tools:[ \t]*(.*)$", re.MULTILINE)
_ITEM_TYPE_RE = re.compile(r"^[ \t]*-?[ \t]*type:[ \t]*(.*)$", re.MULTILINE)
_ITEM_CMD_RE = re.compile(r"^[ \t]*-?[ \t]*cmd:[ \t]*(.*)$", re.MULTILINE)
_ITEM_ARGS_RE = re.compile(r"^([ \t]*-?[ \t]*)args:[ \t]*(.*)$", re.MULTILINE)


def _key_column(item: str) -> int:
    """The column of an item's own keys: the first line's key, after its ``- `` marker."""
    first = item.splitlines()[0] if item else ""
    return len(first) - len(first.lstrip(" -"))


def _own(pattern: re.Pattern[str], item: str) -> re.Match[str] | None:
    """The match of *pattern* on one of the item's OWN keys (at its key column), or ``None``.

    Text at any other indentation (a block scalar's content, a nested mapping) never stands in for the item's
    key, and a key that appears twice is ambiguous (a YAML reader may take either), so both give ``None``.
    """
    col = _key_column(item)
    own = [m for m in pattern.finditer(item) if len(m.group(0)) - len(m.group(0).lstrip(" -")) == col]
    return own[0] if len(own) == 1 else None


_ITEM_KEY_RE = re.compile(r"^[ \t]*-?[ \t]*([A-Za-z_][\w-]*)[ \t]*:", re.MULTILINE)


#: Stands in for a line at the item's key column that isn't a plain ``key:`` (a quoted key, ``? key``, a merge
#: key ``<<:``). YAML reads those as keys too, so a caller must treat this as an unknown key.
UNREADABLE_KEY = "<unreadable-key>"


def _own_keys(item: str) -> list[str]:
    """Every key at the item's own key column, in order (duplicates kept).

    A line at that column that isn't a plain ``key:`` gives :data:`UNREADABLE_KEY`, never silence.
    """
    col = _key_column(item)
    keys: list[str] = []
    for line in item.splitlines():
        stripped = line.lstrip(" -")
        if not stripped or stripped.startswith("#") or len(line) - len(stripped) != col:
            continue
        m = _ITEM_KEY_RE.match(line)
        keys.append(m.group(1) if m else UNREADABLE_KEY)
    return keys


def _scalar(value: str) -> str:
    """A plain or quoted YAML scalar, read the way YAML does: a comment starts only at ``#`` after whitespace."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return re.split(r"\s#", value, maxsplit=1)[0].strip()


def _item_args(item: str) -> list[str] | None:
    """An item's ``args`` as a list, flow (``[a, b]``) or block (``- a``) style; ``None`` if absent or unreadable."""
    m = _own(_ITEM_ARGS_RE, item)
    if not m:
        return None
    value = re.split(r"\s#", m.group(2), maxsplit=1)[0].strip()
    if value.startswith("["):
        # A quoted flow item may hold a comma that this split would misread: unreadable, so it never matches.
        if not value.endswith("]") or any(q in value for q in "\"'"):
            return None
        return [a.strip() for a in value[1:-1].split(",") if a.strip()]
    if value:
        return None
    key_indent = len(m.group(0)) - len(m.group(0).lstrip(" -"))
    out: list[str] = []
    for line in item[m.end():].splitlines()[1:]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= key_indent or not line.strip().startswith("- "):
            break
        out.append(_scalar(line.strip()[2:]))
    return out


def _uncomment(value: str) -> str:
    """Strip an inline ``# comment`` and surrounding quotes/whitespace from a scalar."""
    return value.split("#", 1)[0].strip().strip("\"'").strip()


def recipe_extension_grants(yaml_text: str) -> list[dict[str, Any]] | None:
    """Read a recipe's top-level ``extensions`` as ``[{name, available_tools}]``, failing safe.

    Deliberately not built on :func:`_recipe_section`, which stops at a column-0 line and so would cut
    a zero-indent ``- type:`` list short. Every shape this reader cannot interpret becomes the worst case:
    an entry named :data:`UNPARSED` that callers treat as an unscoped ``developer``.

    Args:
        yaml_text: The recipe YAML.

    Returns:
        * ``None`` when the key is missing, bare or ``null``/``~``: Goose 1.37 then loads the user's
          configured extensions (fail-open).
        * ``[]`` for ``extensions: []`` (with or without a trailing comment).
        * Otherwise one entry per list item, with its ``type`` (``""`` when absent), ``cmd`` and ``args``
          (``None`` when absent or unreadable), its own ``keys`` and ``available_tools``. Only keys at the
          item's own indentation count, and a duplicated key reads as absent. ``available_tools`` is ``None`` when
          the item has no allowlist, an empty one (``[]`` is unrestricted) or one in a form this reader does
          not parse.
        * ``[{"name": UNPARSED, ...}]`` for a flow-style value, or for a second ``extensions:`` key
          (ambiguous: a reader may take either).

    Raises:
        Nothing.
    """
    keys = list(_EXT_KEY_RE.finditer(yaml_text))
    if not keys:
        return None
    if len(keys) > 1:
        return [{"name": UNPARSED, "available_tools": None}]
    value = _uncomment(keys[0].group(1))
    if value in ("null", "~"):
        return None
    if value == "[]":
        return []
    if value:
        return [{"name": UNPARSED, "available_tools": None}]
    block: list[str] = []
    for line in yaml_text[keys[0].end():].splitlines()[1:]:
        if line.strip().startswith("#"):
            continue
        if line and not line[0].isspace() and not line.startswith("- "):
            break
        block.append(line)
    # A new item starts only at the first dash's indentation; deeper "- " lines (an item's args or a
    # block-style allowlist) continue the current item.
    dashes = [len(line) - len(line.lstrip()) for line in block if re.match(r"^\s*-\s", line)]
    item_indent = dashes[0] if dashes else None
    items: list[str] = []
    for line in block:
        if item_indent is not None and re.match(r"^\s*-\s", line) and len(line) - len(line.lstrip()) == item_indent:
            items.append(line)
        elif items and line.strip():
            items[-1] += "\n" + line
    if not items:
        return None
    parsed = []
    for item in items:
        # The item's own name is its shallowest `name:` line; nested content may carry others.
        name = _own(_ITEM_NAME_RE, item)
        allow = _own(_ITEM_ALLOW_RE, item)
        tools = None
        if allow:
            raw = _uncomment(allow.group(1)) if "[" not in allow.group(1) else allow.group(1).split("]", 1)[0] + "]"
            if raw.startswith("[") and raw.endswith("]"):
                tools = [_uncomment(t) for t in raw[1:-1].split(",") if _uncomment(t)] or None
        type_line = _own(_ITEM_TYPE_RE, item)
        ext_type = _scalar(type_line.group(1)) if type_line else ""
        cmd = _own(_ITEM_CMD_RE, item)
        parsed.append({"name": _scalar(name.group(1)) if name else UNPARSED, "available_tools": tools,
                       "type": ext_type, "cmd": _scalar(cmd.group(1)) if cmd else None,
                       "args": _item_args(item), "keys": _own_keys(item)})
    return parsed


def developer_tools(extensions: list[dict[str, Any]]) -> set[str]:
    """Return the ``developer`` tools a parsed extension list exposes.

    An unscoped ``developer`` and any :data:`UNPARSED` entry expose all four tools.

    Args:
        extensions: Output of :func:`recipe_extension_grants`.

    Returns:
        The exposed ``developer`` tool names.

    Raises:
        Nothing.
    """
    tools: set[str] = set()
    for ext in extensions:
        if ext["name"] in ("developer", UNPARSED):
            allow = ext["available_tools"]
            tools |= set(DEVELOPER_TOOLS if allow is None else DEVELOPER_TOOLS & set(allow))
    return tools
