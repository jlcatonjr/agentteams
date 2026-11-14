"""Emission of the optional work-summary Stop hook for Claude teams.

Two files, for a team that has a ``work-summarizer`` agent:

* ``.claude/hooks/work-summary-stop.py`` — the launcher. On a Stop event it runs the
  work-summarizer headlessly with ``--no-session-persistence``, so the run never appears in a
  session list, and keeps the run's output in its own archive outside the project.
* ``.claude/settings.worksummary.example.json`` — the ``Stop`` block an operator may merge.

**This is the one place agentteams ships a hook that starts a model run**, where
``hooks_emit.py`` keeps its bridge hooks to notifications. Three things bound it: it is inert until
an operator merges the example (agentteams never writes ``settings.json``); its example is a
separate file, so wiring the constitutional gate cannot switch it on; and the launcher gates every
run on evidence that the day's summary is stale. Once wired the launcher runs unattended with the
operator's credentials, so its template and this module are pinned in the integrity manifest. It is
not an OS-boundary file, so it stays out of the privilege-artifact and confinement lists.
"""

from __future__ import annotations

import hashlib
from typing import Any

SUMMARIZER_SLUG = "work-summarizer"
LAUNCHER_REL = "../hooks/work-summary-stop.py"
EXAMPLE_REL = "../settings.worksummary.example.json"
#: Placeholder in the example's command, replaced by the SHA-256 of the launcher as emitted.
DIGEST_PLACEHOLDER = "__LAUNCHER_SHA256__"


def _has_summarizer(manifest: dict[str, Any]) -> bool:
    slugs = manifest.get("agent_slug_list") or []
    return any(s == SUMMARIZER_SLUG for s in slugs if isinstance(s, str))


def work_summary_hook_files(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """Return the launcher and its settings example for a team with a work-summarizer.

    Args:
        manifest: The team manifest; ``agent_slug_list`` decides whether anything is emitted.

    Returns:
        ``(relative_path, content)`` tuples, relative to the agents directory. Empty when the team
        has no ``work-summarizer`` or either shipped asset is missing (both or neither, so an
        example never points at a launcher that was not emitted). The example's command carries
        the launcher's SHA-256 and runs the installed script only when it still matches, so the
        digest lives in ``settings.json`` once merged, not beside the script it guards.
    """
    if not _has_summarizer(manifest):
        return []
    from agentteams.frameworks.claude import _read_template_asset  # lazy: claude.py imports this module

    launcher = _read_template_asset("hooks/work-summary-stop.py")
    example = _read_template_asset("hooks/settings.worksummary.example.json")
    if not launcher or not example or DIGEST_PLACEHOLDER not in example:
        return []
    example = example.replace(DIGEST_PLACEHOLDER, hashlib.sha256(launcher.encode("utf-8")).hexdigest())
    return [(LAUNCHER_REL, launcher), (EXAMPLE_REL, example)]


__all__ = ["DIGEST_PLACEHOLDER", "EXAMPLE_REL", "LAUNCHER_REL", "SUMMARIZER_SLUG", "work_summary_hook_files"]
