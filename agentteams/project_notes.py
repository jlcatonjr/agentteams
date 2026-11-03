"""project_notes.py — the USER-EDITABLE "Project-Specific Notes" section of agent personas.

Carved out of ``emit.py`` (CH-07, 2026-10-04) when the shrink-override work pushed it toward the
1000-line ceiling. ``emit`` re-exports both helpers, so ``emit._is_agent_doc`` keeps working.
"""

from __future__ import annotations

from agentteams.fences import PROJECT_NOTES_SECTION as _PROJECT_NOTES_SECTION
from agentteams.fences import _WHOLE_BODY_FENCE, _YAML_FM_RE

_PROJECT_NOTES_HEADING = "## Project-Specific Notes"

_PROJECT_LAYOUT_HEADING = "## Project Layout"
#: Instruction files whose generated Directory Structure can't describe every project (one primary output dir,
#: one reference DB): the project states its real layout here, outside every fence, so --update never touches it.
PROJECT_LAYOUT_SECTION = (
    "\n" + _PROJECT_LAYOUT_HEADING + "\n\n"
    "> ⚙️ **USER-EDITABLE** — this project's real layout where it differs from the generated Directory Structure "
    "and Output Conventions (for example, deliverables under `Projects/*/` rather than one output folder). "
    "Where the two disagree, this section is authoritative. `--update` never overwrites it.\n"
)
_INSTRUCTION_FILES = frozenset({"copilot-instructions.md", "CLAUDE.md", "AGENTS.md"})
_DIRECTORY_STRUCTURE_END = "<!-- AGENTTEAMS:END directory_structure -->"
_BEGIN = "<!-- AGENTTEAMS:BEGIN "


def _ensure_project_layout_section(rel_path: str, content: str) -> str:
    """Insert the USER-EDITABLE 'Project Layout' section after an instruction file's Directory Structure, if absent.

    Only for ``copilot-instructions.md`` / ``CLAUDE.md`` / ``AGENTS.md`` that carry the generated
    ``directory_structure`` fence; inserted directly after its END marker. Idempotent and insert-only, like
    :func:`_ensure_project_notes_section`, so existing files gain it on ``--update --merge``.
    """
    if rel_path.rsplit("/", 1)[-1] not in _INSTRUCTION_FILES or _PROJECT_LAYOUT_HEADING in content:
        return content
    at = content.find(_DIRECTORY_STRUCTURE_END)
    if at < 0:
        return content
    eol = content.find("\n", at)
    eol = len(content) if eol < 0 else eol + 1
    return content[:eol] + PROJECT_LAYOUT_SECTION + content[eol:]


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
    """Add the USER-EDITABLE 'Project-Specific Notes' section if absent.

    Appended, except on a Copilot ``.agent.md``, where it goes before the body's first fence
    (:func:`_copilot_notes_position`). Insert-only: existing content — including project-authored orphan fences
    and hand edits outside the templated structure — is never rewritten, only
    extended. Idempotent: a file that already carries the section is returned
    unchanged. Applied to merged output as well as fresh renders, so existing
    fleet files gain the section on ``--update --merge`` (migration path b).
    """
    content = _ensure_project_layout_section(rel_path, content)  # instruction files only; no-op elsewhere
    if rel_path.endswith(".yaml") and "references/" not in rel_path:
        # A Goose recipe (P5a): the region goes at the end of its `instructions: |` block.
        from agentteams.user_regions import ensure_recipe_notes
        return ensure_recipe_notes(content, _PROJECT_NOTES_SECTION)
    if not _is_agent_doc(rel_path, content):
        return content
    if _PROJECT_NOTES_HEADING in content:
        return content
    if rel_path.endswith(".agent.md"):
        at = _copilot_notes_position(content)
        if at is not None:
            return content[:at] + _PROJECT_NOTES_SECTION.lstrip("\n") + "\n" + content[at:]
    if not content.endswith("\n"):
        content += "\n"
    return content + _PROJECT_NOTES_SECTION


def _copilot_notes_position(content: str) -> int | None:
    """Where a Copilot agent's notes go: just before the body's first fence (baseAgent P4).

    GitHub truncates a custom-agent body past 30,000 characters, and the generated orchestrator and security
    bodies are longer than that, so notes at the end are the first text cut. Placed before the first fence,
    the region still ends at a fence line (:func:`agentteams.user_regions.find_region`), so no template text
    is absorbed into it. ``None`` when the body has no fence, or when its first fence is the whole-body
    ``content`` wrapper, which holds the title too (notes there would precede it): the notes are appended.
    """
    fm = _YAML_FM_RE.match(content)
    body_start = fm.end() if fm else 0
    if content.startswith(_BEGIN, body_start):
        at = body_start
    else:
        at = content.find("\n" + _BEGIN, body_start)
        at = -1 if at < 0 else at + 1
    if at < 0 or content.startswith(f"{_BEGIN}{_WHOLE_BODY_FENCE} ", at):
        return None  # no fence, or a whole-body wrapper that holds the title too: append as elsewhere
    return at
