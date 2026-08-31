"""
toml_write.py — minimal hand-rolled TOML serializer, bounded to the shape
Codex's ``[mcp_servers.<id>]`` tables need (open-items remediation OPEN-6):
strings, string arrays, one level of table nesting. stdlib ``tomllib``
(3.11+) is read-only by design and no TOML-writing dependency is declared
(this project's one runtime dependency is ``jsonschema`` — see
pyproject.toml), so a general-purpose writer is out of scope; this mirrors
the project's existing precedent of narrow, hand-rolled format writers over
general libraries (``agentteams/frameworks/goose.py``'s hand-built YAML).

Not a general TOML library: no support for nested tables beyond one level,
datetimes, floats, or multi-line strings — callers needing those should not
use this module.
"""

from __future__ import annotations

from typing import Any

__all__ = ["render_table", "render_tables"]

_BASIC_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _render_string(value: str) -> str:
    """TOML basic (double-quoted) string, with control characters escaped."""
    out = []
    for ch in value:
        if ch in _BASIC_ESCAPES:
            out.append(_BASIC_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _render_string(value)
    if isinstance(value, int):
        return str(value)
    raise TypeError(
        f"toml_write: unsupported scalar type {type(value).__name__} "
        "(this writer only handles str/bool/int — the shape MCP entries need)"
    )


def _render_array(items: list[Any]) -> str:
    return "[" + ", ".join(_render_scalar(v) for v in items) + "]"


def _render_value(value: Any) -> str:
    if isinstance(value, list):
        return _render_array(value)
    return _render_scalar(value)


def render_table(table_name: str, fields: dict[str, Any]) -> str:
    """Render one ``[table_name]`` table with flat key/value pairs.

    Args:
        table_name: Full dotted table name, e.g. ``"mcp_servers.figma"``.
        fields: Flat mapping of key to str/int/bool/list[str/int/bool].
            Keys with a ``None`` value are omitted (TOML has no null).

    Returns:
        The rendered table, ending with a single trailing newline.
    """
    lines = [f"[{table_name}]"]
    for key, value in fields.items():
        if value is None:
            continue
        lines.append(f"{key} = {_render_value(value)}")
    return "\n".join(lines) + "\n"


def render_tables(tables: dict[str, dict[str, Any]]) -> str:
    """Render multiple tables, each separated by a blank line."""
    return "\n".join(render_table(name, fields) for name, fields in tables.items())
