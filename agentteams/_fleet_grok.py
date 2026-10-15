"""
_fleet_grok.py — Grok Build team classification for ``--fleet`` (Phase 3; carved from fleet.py, CH-07 ceiling).

Mirrors the Codex handling: an agentteams ``.grok/agents`` team is found by its marker; a projection
(``origin: interop``) gets its refresh command and is never ``--update``d; and, unlike codex, a symlinked ``.grok``
tree is reported and never updated, because a native ``--update`` would write agent files through the link
(@security Phase 3 cond. 6; plan tmp/by-week/2026-W42/grok-integration.plan.md §13.1).
"""

from __future__ import annotations

from pathlib import Path

from agentteams.frameworks._agents_md_ownership import symlink_on_path

GROK_MARKER = Path(".grok") / "agents" / "references" / "build-log.json"

#: SKIP-row text per fleet target that must never run.
GROK_SKIP_DETAIL = {
    "grok-symlink": "symlinked .grok tree — never updated through a link; fix the layout by hand",
    "grok-interop": ("interop projection (origin: interop) — refresh by re-running agentteams --interop-from "
                     "<source> --framework grok --output . outside any agent sandbox (fleet never runs it)"),
}


def grok_kind(ws: Path) -> str:
    """Classify a workspace's ``.grok/agents`` team.

    Args:
        ws: The workspace root.

    Returns:
        ``symlink`` (a symlinked ``.grok`` or ``.grok/agents`` that exists), ``interop``, ``direct`` or ``none``.

    Raises:
        Nothing.
    """
    marker = ws / GROK_MARKER
    agents = marker.parent.parent
    if symlink_on_path(agents, ws) is not None:
        return "symlink" if agents.exists() else "none"
    if marker.is_symlink() or not marker.is_file():
        return "none"
    from agentteams.projection_marker import INTEROP_ORIGIN, existing_marker_origin

    return "interop" if existing_marker_origin(agents) == INTEROP_ORIGIN else "direct"


def grok_direct_argv(ws: Path, descriptor: Path | None, shrink_policy: str) -> list[str] | None:
    """Return the native ``--update --merge`` argv for a direct Grok team, like ``codex-direct``.

    Args:
        ws: The workspace root.
        descriptor: The resolved team descriptor, or None.
        shrink_policy: The fleet's ``--shrink-policy``.

    Returns:
        The argv (``normalize_output_path`` appends ``.grok/agents`` to ``--output``), or None without a descriptor.

    Raises:
        Nothing.
    """
    if descriptor is None:
        return None
    argv = ["--description", str(descriptor), "--project", str(ws), "--output", str(ws), "--framework", "grok",
            "--update", "--merge", "--yes", "--shrink-policy", shrink_policy]
    if descriptor.name == "brief.json":
        argv.append("--no-scan")
    return argv

