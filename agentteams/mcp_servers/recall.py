"""recall.py — ``agentteams-recall``: read-only queries of the team's memory index and code index over MCP.

For agents with no shell (Goose readers, Codex read-only agents) that need what ``--query-index`` and
``--query-code`` give. Unlike those CLI commands, this server **never refreshes a stale index**: the CLI rewrites
the index when it is stale, which is a write. It reads the existing files and returns each result set with the
index's build time, so the caller can ask the orchestrator to refresh.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentteams.mcp_servers._stdio import ToolError

SERVER_NAME = "agentteams-recall"
#: Where a team's ``references/`` may live, relative to the project root, in the order searched.
TEAM_DIRS: tuple[str, ...] = (".", ".claude/agents", ".github/agents", ".goose", ".codex/agents")
MAX_K = 20
_KINDS = ("all", "local", "api", "doc")
#: Hit fields passed through (whatever the scorer returns beyond these is dropped); snippets are capped.
_HIT_FIELDS = ("doc_id", "path", "title", "symbol", "kind", "score", "confidence", "snippet")
_SNIPPET_MAX = 400


def _hit(h: dict[str, Any]) -> dict[str, Any]:
    out = {f: h[f] for f in _HIT_FIELDS if f in h}
    if isinstance(out.get("snippet"), str) and len(out["snippet"]) > _SNIPPET_MAX:
        out["snippet"] = out["snippet"][:_SNIPPET_MAX] + "…"
    return out


TOOLS: list[dict[str, Any]] = [
    {"name": "query_index", "description": "Query the team's memory index (documents). Read-only; never refreshes.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string", "minLength": 1, "maxLength": 500},
         "k": {"type": "integer", "minimum": 1, "maximum": MAX_K}}}},
    {"name": "query_code", "description": "Query the team's code and API index. Read-only; never refreshes.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string", "minLength": 1, "maxLength": 500},
         "k": {"type": "integer", "minimum": 1, "maximum": MAX_K},
         "kind": {"type": "string", "enum": list(_KINDS)}}}},
]


#: Index locations relative to a team dir (the same paths the CLI writes).
MEMORY_INDEX = "references/memory-index.json"
CODE_INDEX_DIR = "references/code-index"


def _inside(root: Path, path: Path) -> Path:
    """``path`` resolved, refused unless it stays inside the project (symlinks and ``..`` included)."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ToolError(f"{path.name} resolves outside the project; refusing")
    return resolved


def _team_dir(root: Path, marker: str) -> Path:
    for rel in TEAM_DIRS:
        candidate = root / rel
        if (candidate / marker).exists():
            return _inside(root, candidate)
    raise ToolError(f"no {marker} under {', '.join(TEAM_DIRS)}; ask the orchestrator to run --refresh-index")


def _load(root: Path, path: Path, schema: str, label: str) -> Any:
    """Read and schema-validate one index file. Uncached on purpose: the CLI's loaders record a validation
    sidecar (``.vcache``), which is a write."""
    from agentteams.cli.schema_cache import _schema_path, _validate_against_schema

    try:
        raw = _inside(root, path).read_bytes()
        data = json.loads(raw)
        _validate_against_schema(data, _schema_path(schema).read_bytes(), error_cls=ToolError, label=label)
    except (OSError, ValueError) as exc:
        raise ToolError(f"cannot read {label} at {path.name}: {exc}") from exc
    return data


def _code_partitions(root: Path, team: Path) -> dict[str, Any]:
    cache = team / CODE_INDEX_DIR
    manifest = _load(root, cache / "manifest.json", "code-index.schema.json", "code index")
    parts: dict[str, Any] = {}
    for name, meta in (manifest.get("partitions") or {}).items():
        rel = str((meta or {}).get("file", f"{name}.json"))
        path = cache / rel
        if Path(rel).is_absolute() or not path.exists():
            continue
        parts[name] = _load(root, path, "code-index.schema.json", "code index")
    return parts


def _args(arguments: dict[str, Any]) -> tuple[str, int]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ToolError("query must be a non-empty string of at most 500 characters")
    k = arguments.get("k", 5)
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
        raise ToolError(f"k must be an integer from 1 to {MAX_K}")
    return query, k


def call(name: str, arguments: dict[str, Any], root: Path) -> Any:
    """Run one tool.

    Args:
        name: ``query_index`` or ``query_code``.
        arguments: The tool arguments.
        root: The project root.

    Returns:
        ``{"built_at", "hits"}``; paths in hits are as the index stores them (project-relative).

    Raises:
        ToolError: Bad arguments, or no index to read.
    """
    query, k = _args(arguments)
    if name == "query_index":
        from agentteams.memory_index import query_index

        index = _load(root, _team_dir(root, MEMORY_INDEX) / MEMORY_INDEX, "memory-index.schema.json", "memory index")
        hits = query_index(index, query, k=k)
        return {"built_at": index.get("built_at"), "may_be_stale": True, "hits": [_hit(h) for h in hits]}
    if name != "query_code":
        raise ToolError(f"unknown tool {name!r}")
    kind = arguments.get("kind", "all")
    if kind not in _KINDS:
        raise ToolError(f"kind must be one of {', '.join(_KINDS)}")
    from agentteams import code_index as ci

    parts = _code_partitions(root, _team_dir(root, f"{CODE_INDEX_DIR}/manifest.json"))
    hits = ci.query_partitions(parts, query, k=k, kind=kind)
    built = sorted({str(p.get("built_at")) for p in parts.values() if isinstance(p, dict) and p.get("built_at")})
    return {"built_at": built[-1] if built else None, "may_be_stale": True, "hits": [_hit(h) for h in hits]}
