"""copilot_body_limit.py — ``--check``'s guard for operator text past Copilot's agent-body limit (baseAgent P4).

GitHub documents a 30,000-character limit on a custom agent's body (the text after the YAML front matter) and
may truncate past it. The generated orchestrator and security bodies already exceed it from the templates
alone, so an overrun by itself is reported, not failed: failing it would turn every Copilot team's ``--check``
red until those templates shrink. What ``--check`` fails on is the harm the limit causes an operator: a
``## Project-Specific Notes`` region holding the operator's own text that starts past the limit, where a
truncation removes it first. Fresh renders place the notes before the first fence
(:func:`agentteams.project_notes._copilot_notes_position`); files rendered earlier keep them at the end until
the operator moves them.

Measured on disk, so the operator's text counts, which the render-time warning in
:mod:`agentteams.frameworks.copilot_vscode` cannot see.

Stdlib only.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.frameworks.copilot_vscode import MAX_BODY_CHARS
from agentteams.user_regions import HEADINGS, find_region
from agentteams.yaml_frontmatter import parse_yaml_front_matter

_NOTES_HEADING = HEADINGS[0]
#: Frameworks whose agent bodies the limit applies to.
COPILOT_FRAMEWORKS = frozenset({"copilot-vscode", "copilot-cli"})


def _has_operator_text(region: str) -> bool:
    """Whether a notes region holds more than its heading and the generated USER-EDITABLE note.

    The generated note is the first blockquote paragraph after the heading; anything after it, including an
    operator's own blockquote, is operator text.
    """
    lines = region.splitlines()[1:]
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and "USER-EDITABLE" in lines[i] and lines[i].lstrip().startswith(">"):
        while i < len(lines) and lines[i].lstrip().startswith(">"):
            i += 1
    return any(ln.strip() for ln in lines[i:])


def body_limit_findings(output_dir: Path) -> tuple[list[str], list[str]]:
    """Measure every Copilot agent body under *output_dir*.

    Args:
        output_dir: The team's agents directory (``.github/agents``).

    Returns:
        ``(failures, advisories)``: one failure per agent whose notes hold operator text and start past the
        limit; one advisory per other agent whose body is over the limit.

    Raises:
        Nothing. An unreadable file is reported as an advisory.
    """
    failures: list[str] = []
    advisories: list[str] = []
    for path in sorted(output_dir.glob("*.agent.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            advisories.append(f"{path.name}: not measured ({type(exc).__name__})")
            continue
        yaml_text, body = parse_yaml_front_matter(text)
        body = body if yaml_text is not None else text
        if len(body) <= MAX_BODY_CHARS:
            continue
        span = find_region(body, _NOTES_HEADING)
        if span is not None and span[0] > MAX_BODY_CHARS and _has_operator_text(body[span[0]:span[1]]):
            failures.append(
                f"{path.name}: its Project-Specific Notes start at character {span[0]:,} of a {len(body):,}-character "
                f"body, past Copilot's {MAX_BODY_CHARS:,}-character limit, so a truncation removes them first. Move "
                "the region to just before the body's first <!-- AGENTTEAMS:BEGIN fence (outside every fence; "
                "--update --merge keeps it there)."
            )
        else:
            advisories.append(f"{path.name}: body is {len(body):,} characters (over {MAX_BODY_CHARS:,}); no "
                              "operator notes past the limit")
    return failures, advisories


__all__ = ["COPILOT_FRAMEWORKS", "MAX_BODY_CHARS", "body_limit_findings"]
