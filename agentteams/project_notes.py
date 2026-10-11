"""project_notes.py — the USER-EDITABLE "Project-Specific Notes" section of agent personas.

Carved out of ``emit.py`` (CH-07, 2026-10-04) when the shrink-override work pushed it toward the
1000-line ceiling. ``emit`` re-exports both helpers, so ``emit._is_agent_doc`` keeps working.
"""

from __future__ import annotations

from agentteams.fences import PROJECT_NOTES_SECTION as _PROJECT_NOTES_SECTION
from agentteams.fences import _YAML_FM_RE

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
    """Append the USER-EDITABLE 'Project-Specific Notes' section if absent.

    Pure append: existing content — including project-authored orphan fences
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
    if not content.endswith("\n"):
        content += "\n"
    return content + _PROJECT_NOTES_SECTION
