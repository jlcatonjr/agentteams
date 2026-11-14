"""Authority sources from a Markdown brief's authority or sources section (#392 item 11).

Split from :mod:`agentteams.ingest` (CH-07 ceiling). A list item may be ``**Name** (`path`) — scope`` (the form
agentteams renders), ``Name: path — scope``, ``name | path`` or bare text; a Markdown table is read by column.
"""

from __future__ import annotations

import re
from typing import Any

# Unlike ingest._LIST_MARKER_RE (one marker, for the goal fallback), this strips stacked markers ("- 1. ") and "•".
_AUTHORITY_ITEM_MARKER = re.compile(r"^(?:(?:[-*•+]|\d+[.)])\s+)+")
_TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?$")
_BOLD_ENTRY = re.compile(r"^\*\*(?P<name>.+?)\*\*\s*(?:\(`(?P<path>[^`]*)`\))?\s*(?:(?:—|–|--?)\s*(?P<scope>.*))?$")
# Whole header words that name a column, checked path, then scope, then name, so "Source Path" is a path.
_COLUMN_WORDS = (
    ("path", frozenset({"path", "location", "file", "filename", "url", "directory", "dir"})),
    ("scope", frozenset({"scope", "purpose", "covers", "governs", "description", "notes", "role"})),
    ("name", frozenset({"name", "source", "authority", "repository", "repo", "document", "title"})),
)
# Columns that only number the rows; never used as a name, path or scope.
_ORDINAL_WORDS = frozenset({"tier", "rank", "order", "no", "number", "priority"})


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _authority_entry(item: str) -> dict[str, Any]:
    """Parse one list item: ``**Name** (`path`) — scope``, ``Name: path — scope``, ``name | path`` or bare."""
    bold = _BOLD_ENTRY.match(item)
    if bold:
        entry: dict[str, Any] = {"name": bold["name"].strip(), "path": (bold["path"] or "").strip()}
        if bold["scope"]:
            entry["scope"] = bold["scope"].strip()
        return entry
    if item.startswith("**") and ("**:" in item or ":**" in item):  # **Name**: path or **Name:** path
        item = item[2:].replace("**:", ":", 1).replace(":**", ":", 1)
    if ":" in item:
        name, rest = item.split(":", 1)
        path_scope = rest.strip().split(" — ", 1)
        entry = {"name": name.strip(), "path": path_scope[0].strip()}
        if len(path_scope) > 1:
            entry["scope"] = path_scope[1].strip()
        return entry
    if "|" in item:
        parts = item.split("|")
        return {"name": parts[0].strip(), "path": parts[1].strip() if len(parts) > 1 else ""}
    return {"name": item, "path": item}


def _header_columns(header: list[str], *, all_named: bool = False) -> dict[str, int] | None:
    """Map name/path/scope to header cells by whole words; row-number columns (``#``, Tier) are never used.

    Args:
        header: The header row's cells.
        all_named: Without a separator row, accept the row as a header only if every other cell names a column.

    Returns:
        Column indexes by key, or ``None`` when ``all_named`` is set and the row is not a header.
    """
    found: dict[str, int] = {}
    ordinal: set[int] = set()
    unnamed = 0
    for i, cell in enumerate(header):
        words = set(re.findall(r"[a-z]+", cell.lower()))
        if not words or words <= _ORDINAL_WORDS:
            ordinal.add(i)
            continue
        key = next((k for k, names in _COLUMN_WORDS if k not in found and words & names), None)
        if key is None:
            unnamed += 1
        else:
            found[key] = i
    if all_named and (unnamed or not found):
        return None
    free = [i for i in range(len(header)) if i not in ordinal and i not in found.values()]
    for key in ("name", "path", "scope"):
        if key not in found and free:
            found[key] = free.pop(0)
    return found


def _authority_table(table: list[str]) -> list[dict[str, Any]]:
    """Rows of one Markdown table as sources, by column; header and separator rows are skipped."""
    sep = next((i for i, ln in enumerate(table) if _TABLE_SEPARATOR.match(ln)), None)
    rows = [_table_cells(ln) for ln in table if not _TABLE_SEPARATOR.match(ln)]  # every separator row
    if sep:  # the row above a separator is the header, whatever it says
        columns = _header_columns(rows.pop(sep - 1))
    else:  # no separator: a first row that only names columns is still a header
        columns = _header_columns(rows[0], all_named=True) if rows else None
        if columns is not None:
            rows = rows[1:]
    if columns is None:
        columns = {"name": 0, "path": 1, "scope": 2}
    sources = []
    for row in rows:
        name, path, scope = (row[columns[k]].strip("*` ") if k in columns and columns[k] < len(row) else ""
                             for k in ("name", "path", "scope"))
        if name or path:
            entry: dict[str, Any] = {"name": name or path, "path": path}
            if scope:
                entry["scope"] = scope
            sources.append(entry)
    return sources


def has_list_or_table(text: str) -> bool:
    """Whether a section body holds a list item or a Markdown table row.

    Args:
        text: The section body.

    Returns:
        True when any line starts with a list marker or ``|``.
    """
    return any(ln.strip().startswith("|") or _AUTHORITY_ITEM_MARKER.match(ln.strip()) for ln in text.splitlines())


def parse_authority_sources(text: str) -> list[dict[str, Any]]:
    """Parse an authority section's list items and Markdown tables into ranked sources.

    Tables are parsed by column, skipping header and separator rows; list markers are stripped without eating a
    bold name's ``**``. When the section has any list item or table, other prose lines are ignored; otherwise
    each line is an entry, as before (#392 item 11).

    Args:
        text: The section body.

    Returns:
        Sources in document order, each with ``name``, ``path``, ``rank`` and, when given, ``scope``.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    structured = has_list_or_table(text)
    sources: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("|"):
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            sources += _authority_table(lines[i:j])
            i = j
            continue
        marked = _AUTHORITY_ITEM_MARKER.match(lines[i])
        item = _AUTHORITY_ITEM_MARKER.sub("", lines[i]).strip()
        if item and (marked or not structured):
            sources.append(_authority_entry(item))
        i += 1
    for rank, entry in enumerate(sources, start=1):
        entry["rank"] = rank
    return sources
