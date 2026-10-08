"""First-party MCP servers shipped with agentteams, served by ``agentteams --serve-mcp NAME``.

Each server is stdlib-only, read-only, opens no network connection and holds no key. The catalogue that binds
them to agents lives in ``agentteams/templates/mcp/`` (see ``agentteams.mcp_catalog``).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

#: Server name -> module that defines ``SERVER_NAME``, ``TOOLS`` and ``call(name, arguments, root)``.
SERVERS: dict[str, str] = {
    "recall": "agentteams.mcp_servers.recall",
    "gitread": "agentteams.mcp_servers.gitread",
}


def run_server(name: str, root: Path) -> int:
    """Serve one first-party MCP server over stdio until stdin closes.

    Args:
        name: A key of :data:`SERVERS`.
        root: The project root the server reads.

    Returns:
        The process exit code (0 on a clean close, 2 for an unknown server).

    Raises:
        Nothing: an unknown name is reported on stderr.
    """
    if name not in SERVERS:
        print(f"[serve-mcp] unknown server {name!r}; choose from {', '.join(sorted(SERVERS))}", file=sys.stderr)
        return 2
    from agentteams.mcp_servers._stdio import serve

    mod = importlib.import_module(SERVERS[name])
    return serve(mod.SERVER_NAME, mod.TOOLS, mod.call, root)
