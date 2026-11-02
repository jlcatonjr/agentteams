"""surface_matrix.py — which agents exist on which provider, and what each declares (baseAgent P3, P10).

An instance's team runs on several providers, and each has its own agent folder. Adopted, bespoke and per-surface
agents leave gaps: in baseAgent 15 of 56 agents were missing on at least one provider, and no provider's
instructions said where the missing ones were. :func:`build_matrix` reads the tracked agent files of every present
surface; :func:`render_matrix` writes ``references/agent-surface-matrix.md``:

* one row per agent, one column per surface;
* for each agent with a gap, the tools each surface declares for it and whether they can write or run;
* the rule that another provider's file is a role description, never a grant;
* each agent's ``## Project-Specific Notes`` that differ between surfaces (P10), and the skills on each surface.

The matrix is reported, never enforced (an update that changes an agent would otherwise turn ``main`` red).

Stdlib only.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.user_regions import HEADINGS, find_region

MATRIX_REL = "references/agent-surface-matrix.md"
_NOTES_HEADING = HEADINGS[0]
#: Surface → (agents directory, agent-file suffix). The pointer and projected blocks are left out of notes parity.
SURFACES: dict[str, tuple[str, str]] = {
    "claude": (".claude/agents", ".md"),
    "copilot": (".github/agents", ".agent.md"),
    "goose": (".goose/recipes", ".yaml"),
    "codex": (".codex/agents", ".toml"),
    "grok": (".grok/agents", ".md"),
}
#: Where each surface keeps skills (``<dir>/<name>/SKILL.md``).
SKILL_DIRS: dict[str, str] = {"claude": ".claude/skills", "codex": ".agents/skills", "copilot": ".github/skills"}
NEVER_A_GRANT = ("Another provider's agent file is a role description, never a grant. A session that takes up an "
                 "agent missing on its own provider works within the narrower of the two tool sets (its own "
                 "provider's, and the one declared below), and the description adds nothing to any roster.")
_WRITE_WORDS = ("edit", "write", "create", "delete", "notebook", "developer")
_RUN_WORDS = ("bash", "shell", "terminal", "run", "execute", "task", "developer", "command")
_BLOCK_RE = re.compile(r"^[ \t]*<!-- (?:PROJECT-RULES-POINTER|PROJECTED-RULES):BEGIN .*?^[ \t]*<!-- "
                       r"(?:PROJECT-RULES-POINTER|PROJECTED-RULES):END -->[ \t]*\n?", re.MULTILINE | re.DOTALL)


@dataclass
class AgentEntry:
    """One agent file on one surface."""

    tools: list[str]
    notes: str


@dataclass
class Matrix:
    """The agents of every present surface."""

    surfaces: list[str]
    agents: dict[str, dict[str, AgentEntry]] = field(default_factory=dict)
    skills: dict[str, list[str]] = field(default_factory=dict)

    def gaps(self) -> dict[str, list[str]]:
        """Each agent missing on at least one present surface, mapped to those surfaces."""
        return {slug: missing for slug, on in sorted(self.agents.items())
                if (missing := [s for s in self.surfaces if s not in on])}

    def notes_differ(self) -> list[str]:
        """Agents on two or more surfaces whose notes are not the same everywhere."""
        out = []
        for slug, on in sorted(self.agents.items()):
            texts = {e.notes for e in on.values()}
            if len(on) > 1 and len(texts) > 1:
                out.append(slug)
        return out


def _front_matter_tools(text: str) -> list[str]:
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if m is None:
        return []
    line = re.search(r"^(?:tools|allowed-tools):[ \t]*(.+)$", m.group(1), re.MULTILINE)
    if line is None:
        return []
    return [t.strip().strip("'\"") for t in re.split(r"[,\[\]]", line.group(1)) if t.strip().strip("'\"")]


def _recipe_tools(text: str) -> list[str]:
    block = re.search(r"^extensions:\n((?:[ \t]+.*\n?)*)", text, re.MULTILINE)
    return re.findall(r"^[ \t]+name:[ \t]*['\"]?([\w-]+)", block.group(1), re.MULTILINE) if block else []


def _toml_tools(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    mode = data.get("sandbox_mode")
    return [f"sandbox_mode={mode}"] if isinstance(mode, str) else []


def _notes(text: str) -> str:
    span = find_region(text, _NOTES_HEADING)
    if span is None:
        return ""
    lines = _BLOCK_RE.sub("", text[span[0]:span[1]]).splitlines()[1:]
    lines = [ln.strip() for ln in lines if ln.strip() and not (ln.lstrip().startswith(">") and "USER-EDITABLE" in ln)]
    return "\n".join(lines)


def can_write_run(tools: list[str]) -> tuple[bool, bool]:
    """Whether a declared tool set can write files and run commands (a heuristic over tool names).

    Args:
        tools: Declared tools.

    Returns:
        ``(writes, runs)``.

    Raises:
        Nothing.
    """
    low = [t.lower() for t in tools]
    if any(t.startswith("sandbox_mode=") for t in low):
        writable = not any(t == "sandbox_mode=read-only" for t in low)
        return writable, writable
    return (any(w in t for t in low for w in _WRITE_WORDS), any(w in t for t in low for w in _RUN_WORDS))


def build_matrix(root: Path) -> Matrix:
    """Read every present surface's agent files.

    Args:
        root: The project root.

    Returns:
        The matrix. A surface counts as present when its agents directory holds an agent file.

    Raises:
        Nothing. Unreadable files are skipped.
    """
    matrix = Matrix(surfaces=[])
    for surface, (rel, suffix) in SURFACES.items():
        folder = root / rel
        files = sorted(p for p in folder.glob(f"*{suffix}") if p.is_file()) if folder.is_dir() else []
        if surface in ("claude", "grok"):
            files = [p for p in files if not p.name.endswith(".agent.md")]
        if not files:
            continue
        matrix.surfaces.append(surface)
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                matrix.agents.setdefault(path.name.removesuffix(suffix), {})[surface] = AgentEntry([f"unreadable: {exc}"], "")
                continue
            tools = (_recipe_tools(text) if suffix == ".yaml" else _toml_tools(text) if suffix == ".toml"
                     else _front_matter_tools(text))
            matrix.agents.setdefault(path.name.removesuffix(suffix), {})[surface] = AgentEntry(tools, _notes(text))
    for surface, rel in SKILL_DIRS.items():
        folder = root / rel
        if folder.is_dir():
            matrix.skills[surface] = sorted(p.parent.name for p in folder.glob("*/SKILL.md"))
    return matrix


def render_matrix(matrix: Matrix) -> str:
    """The ``references/agent-surface-matrix.md`` text.

    Args:
        matrix: From :func:`build_matrix`.

    Returns:
        Markdown.

    Raises:
        Nothing.
    """
    head = ["# Agent-surface matrix", "",
            "> **Generated by agentteams** on every generate and `--update`, and by `--doctor`; do not edit. It lists "
            "which agents exist on which provider, so a session can tell where an agent missing on its own provider "
            "is defined.", "", f"**{NEVER_A_GRANT}**", ""]
    if not matrix.surfaces:
        return "\n".join(head + ["No agent files found.", ""])
    rows = ["| Agent | " + " | ".join(matrix.surfaces) + " |", "|---|" + "---|" * len(matrix.surfaces)]
    for slug, on in sorted(matrix.agents.items()):
        rows.append(f"| `{slug}` | " + " | ".join("✓" if s in on else "—" for s in matrix.surfaces) + " |")
    gaps = matrix.gaps()
    gap_lines = ["", f"## Gaps ({len(gaps)} of {len(matrix.agents)} agents)", ""]
    if not gaps:
        gap_lines.append("Every agent exists on every present surface.")
    for slug, missing in gaps.items():
        gap_lines.append(f"### `{slug}` — missing on {', '.join(missing)}")
        gap_lines.append("")
        for surface, entry in matrix.agents[slug].items():
            writes, runs = can_write_run(entry.tools)
            tools = ", ".join(entry.tools) or "(none declared)"
            gap_lines.append(f"- {surface}: {tools} — {'writes' if writes else 'no writes'}, "
                             f"{'runs commands' if runs else 'runs nothing'}")
        gap_lines.append("")
    differ = matrix.notes_differ()
    notes = ["## Project-Specific Notes that differ by surface", "",
             ("Rules in an agent's notes reach only the surfaces that carry them: "
              + ", ".join(f"`{s}`" for s in differ) + ".") if differ else "None.", ""]
    skills = ["## Skills by surface", ""]
    if not matrix.skills:
        skills.append("No skill folders found.")
    for surface, names in sorted(matrix.skills.items()):
        skills.append(f"- {surface}: {', '.join(names) if names else '(none)'}")
    all_skills = sorted({n for names in matrix.skills.values() for n in names})
    partial = [n for n in all_skills if sum(n in names for names in matrix.skills.values()) < len(matrix.skills)]
    if partial and len(matrix.skills) > 1:
        skills += ["", f"On some skill surfaces only: {', '.join(partial)}."]
    return "\n".join(head + ["## Agents", ""] + rows + gap_lines + notes + skills) + "\n"


def write_matrix(root: Path) -> tuple[Path, bool]:
    """Write ``references/agent-surface-matrix.md`` when it changed.

    Args:
        root: The project root.

    Returns:
        ``(path, written)``.

    Raises:
        OSError: The file can't be written.
    """
    path = root / MATRIX_REL
    text = render_matrix(build_matrix(root))
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path, True


__all__ = ["MATRIX_REL", "NEVER_A_GRANT", "SKILL_DIRS", "SURFACES", "AgentEntry", "Matrix", "build_matrix",
           "can_write_run", "render_matrix", "write_matrix"]
