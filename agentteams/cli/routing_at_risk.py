"""Routed agents an ``--update --overwrite`` would orphan (issue #392 item 12, the report half).

``--overwrite`` replaces the on-disk orchestrator with the freshly rendered one. Any agent the old orchestrator
routes work to, but this build does not emit, loses its routing: a bespoke workflow step or routing row naming
``@my-agent`` is gone after the run. Before this, only the stderr "adopted by merge" advisory named such agents,
and only those with a file on disk.

This module lists every ``@slug`` the on-disk orchestrator's body names in backticks (the form agentteams renders
and bespoke edits copy) that the build will not emit, with or without a file. The report is printed on stderr
before any file is written, and in a dry run it also goes into the ``--json`` report. The run still proceeds: the
refusal half of item 12 is a separate change, after ``@security`` reviews the overwrite gate.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

#: A backticked agent mention: `@slug`, as agentteams renders routing rows and workflow steps.
_MENTION = re.compile(r"`@([a-z0-9][a-z0-9-]{0,63})`")
#: Chat participants and tools that templates mention in backticks but that are never team agents.
_NOT_AGENTS = frozenset({"workspace", "vscode", "terminal", "github"})


def _body(text: str) -> str:
    """The text after a leading ``---`` front-matter block (the whole text when there is none)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def mentioned_slugs(text: str) -> set[str]:
    """Slugs an orchestrator's body names as backticked ``@slug``, excluding chat participants.

    Args:
        text: The orchestrator file's text; a leading front-matter block is skipped.

    Returns:
        The named slugs.
    """
    return set(_MENTION.findall(_body(text))) - _NOT_AGENTS


def at_risk_routed_slugs(output_dir: Path, agent_ext: str, emitted: set[str], rendered: str) -> list[dict[str, Any]]:
    """Agents the on-disk orchestrator routes to that neither this build nor its new orchestrator keeps.

    Args:
        output_dir: The team's agents directory.
        agent_ext: The framework's agent-file extension (``.agent.md``, ``.md``, ...).
        emitted: Slugs this build emits as agents.
        rendered: The freshly rendered orchestrator; a slug it still names (an ``(if in team)`` row) keeps its routing.

    Returns:
        One ``{"slug", "has_file"}`` dict per at-risk slug, sorted by slug; empty when there is no orchestrator.
    """
    try:
        text = (output_dir / f"orchestrator{agent_ext}").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    named = mentioned_slugs(text) - mentioned_slugs(rendered) - emitted
    return [{"slug": s, "has_file": (output_dir / f"{s}{agent_ext}").is_file()} for s in sorted(named)]


def report_at_risk_routing(output_dir: Path, manifest: dict, adapter: Any, rendered_files: list[tuple[str, str]],
                           *, overwrite: bool) -> list[dict[str, Any]]:
    """Print, on stderr, the routed agents an ``--overwrite`` would orphan, and return them for the dry-run report.

    Args:
        output_dir: The team's agents directory.
        manifest: The build manifest; ``agent_slug_list`` and ``adopted_agents`` (slugs) are what it emits.
        adapter: The framework adapter, for the agent-file extension.
        rendered_files: This build's ``(relative path, content)`` pairs; the orchestrator's is compared.
        overwrite: Whether this is an ``--overwrite`` run; a merge keeps the orchestrator's body, so it reports nothing.

    Returns:
        The at-risk entries (see :func:`at_risk_routed_slugs`); empty for a merge.
    """
    if not overwrite:
        return []
    emitted = set(manifest.get("agent_slug_list", []) or [])
    emitted |= {a for a in manifest.get("adopted_agents", []) or [] if isinstance(a, str)}
    ext = adapter.get_file_extension("agent")
    rendered = next((c for p, c in rendered_files if Path(p).name == f"orchestrator{ext}"), "")
    at_risk = at_risk_routed_slugs(output_dir, ext, emitted, rendered)
    if at_risk:
        print(f"  !  --overwrite replaces the orchestrator: {len(at_risk)} agent(s) it routes to are not in this "
              "build and lose their routing:", file=sys.stderr)
        for entry in at_risk:
            where = "file kept on disk" if entry["has_file"] else "no file"
            print(f"       @{entry['slug']}  — {where}", file=sys.stderr)
        print("     To keep them: --adopt-orphans (agents with a file), add them to the brief, "
              "or use --update --merge.", file=sys.stderr)
    return at_risk


__all__ = ["at_risk_routed_slugs", "mentioned_slugs", "report_at_risk_routing"]
