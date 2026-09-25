"""Cross-repo coordination wiring for the Goose adapter (Phase 2).

Carved from ``goose.py`` to keep it under the CH-07 module-size ceiling, alongside the earlier
``goose_docs`` / ``goose_recipe_read`` / ``goose_recipe_validate`` carves. Holds the coordination
gate and the fixed stdio extension entry for the first-party coordination MCP server
(``goose_docs._coordination_mcp_content`` ships the server script itself).

File-based / human-in-the-loop model: the server these wire in only READS or RECORDS — it never
grants clearance, authorizes, or executes anything in another repo (autonomous execution is the
separate, HALTed design in ``references/plans/goose-autonomous-handoff-authorization.design.md``).
"""

from __future__ import annotations

from typing import Any

#: Slugs that receive the coordination MCP extension when the team declares coordination roots.
#: PoLA: only the actual coordinators — the orchestrator (entry point / routes coordination) and
#: repo-liaison (the cross-repo Protocol-3 agent). @security is deliberately EXCLUDED: it reviews
#: and relays (reading files directly and appending *signed* verdicts via the decision-log path),
#: so it needs neither the record-only coordination tools nor a widened capability surface.
COORDINATION_AGENT_SLUGS = frozenset({"orchestrator", "repo-liaison"})


def coordination_enabled(manifest: dict[str, Any]) -> bool:
    """Return True iff the team declares ``coordination_write_roots`` (the coordination gate).

    Presence is the single gate: it ships the coordination MCP server (``extra_output_files``)
    and wires it into the coordinator/liaison recipes. Absent, the team stays byte-identical to
    baseline (no extra file, no recipe change).

    Args:
        manifest: The team manifest.

    Returns:
        True when ``coordination_write_roots`` is present and non-empty, else False.
    """
    return bool(manifest.get("coordination_write_roots"))


def coordination_extension() -> dict[str, Any]:
    """Return the fixed stdio MCP extension entry for the shipped coordination server.

    First-party (not an operator ``mcp_servers[]`` entry), so it bypasses the ``goose:mcp`` opt-in
    and the ``_goose_wirable`` read-only bar (which protect operator-supplied servers) — it ships
    with the team like the resilient runner. stdio (no network) survives a confined profile's
    ``deny network*``. The timeout is imported (function-local, to avoid an import cycle) from
    ``goose`` so it stays single-sourced with the operator-MCP wiring.
    """
    from agentteams.frameworks.goose import _MCP_EXT_TIMEOUT

    return {
        "type": "stdio", "name": "agentteams_coordination", "cmd": "python3",
        "args": ["scripts/goose-coordination-mcp.py"], "env_keys": [], "timeout": _MCP_EXT_TIMEOUT,
    }
