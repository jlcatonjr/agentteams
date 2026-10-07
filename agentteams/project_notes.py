"""project_notes.py — the USER-EDITABLE "Project-Specific Notes" section of agent personas.

Carved out of ``emit.py`` (CH-07, 2026-10-04) when the shrink-override work pushed it toward the
1000-line ceiling. ``emit`` re-exports both helpers, so ``emit._is_agent_doc`` keeps working.
"""

from __future__ import annotations

from agentteams.fences import PROJECT_NOTES_SECTION as _PROJECT_NOTES_SECTION
from agentteams.fences import _YAML_FM_RE

_PROJECT_NOTES_HEADING = "## Project-Specific Notes"


def _is_agent_doc(rel_path: str, content: str) -> bool:
    """Return True when rel_path/content is a generated agent persona document.

    Agent personas carry YAML front matter; reference files, instruction files,
    and SETUP-REQUIRED.md do not and are excluded.
    """
    if not rel_path.endswith(".md"):
        return False
    base = rel_path.rsplit("/", 1)[-1]
    if "references/" in rel_path:
        return False
    # Skills (operational tool docs) carry front matter but are not agent
    # personas — they must not get the "Project-Specific Notes" persona section.
    if rel_path.startswith("../skills/") or "/skills/" in rel_path:
        return False
    if base in {"copilot-instructions.md", "CLAUDE.md", "AGENTS.md", "SETUP-REQUIRED.md"}:
        return False
    return bool(_YAML_FM_RE.match(content))


def _ensure_project_notes_section(rel_path: str, content: str) -> str:
    """Append the USER-EDITABLE 'Project-Specific Notes' section if absent.

    Pure append: existing content — including project-authored orphan fences
    and hand edits outside the templated structure — is never rewritten, only
    extended. Idempotent: a file that already carries the section is returned
    unchanged. Applied to merged output as well as fresh renders, so existing
    fleet files gain the section on ``--update --merge`` (migration path b).
    """
    if rel_path.endswith(".yaml") and "references/" not in rel_path:
        # A Goose recipe (P5a): the region goes at the end of its `instructions: |` block.
        from agentteams.user_regions import ensure_recipe_notes
        return ensure_recipe_notes(content, _PROJECT_NOTES_SECTION)
    if not _is_agent_doc(rel_path, content):
        return content
    if _PROJECT_NOTES_HEADING in content:
        return content
    if not content.endswith("\n"):
        content += "\n"
    return content + _PROJECT_NOTES_SECTION
