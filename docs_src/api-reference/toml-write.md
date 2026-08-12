# `toml_write`

Minimal hand-rolled TOML serializer, bounded to the shape Codex's
`[mcp_servers.<id>]` tables need (open-items remediation OPEN-6): strings, string
arrays, one level of table nesting. stdlib `tomllib` (3.11+) is read-only by
design and no TOML-writing dependency is declared (this project's one runtime
dependency is `jsonschema` — see `pyproject.toml`), so a general-purpose writer is
out of scope. This mirrors the project's existing precedent of narrow, hand-rolled
format writers over general libraries (`agentteams/frameworks/goose.py`'s
hand-built YAML).

**Not a general TOML library.** No support for nested tables beyond one level,
datetimes, floats, or multi-line strings — callers needing those should not use
this module.

## Public Surface

```python
render_table(table_name: str, fields: dict[str, Any]) -> str
```
Render one `[table_name]` table with flat key/value pairs. `table_name` is a full
dotted name (e.g. `"mcp_servers.figma"`). Values may be `str`/`int`/`bool`, or a
list of those; `None` values are omitted (TOML has no null). Strings are rendered
as TOML basic (double-quoted) strings with control characters escaped. Any other
scalar type raises `TypeError`.

```python
render_tables(tables: dict[str, dict[str, Any]]) -> str
```
Render multiple tables, each separated by a blank line.

## Correctness

Every value the writer emits round-trips through stdlib `tomllib.loads` back to the
same Python value — validated directly rather than assumed, since this is the one
property a hand-rolled serializer must never get wrong.

Used exclusively by [`codex_mcp_emit`](codex-mcp-emit.md).
