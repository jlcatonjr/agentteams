"""hygiene_rules.py — the code-hygiene rule catalogue's project rules: migration and id checks (IM item 26).

Projects number their own hygiene rules ``CH-P01``, ``CH-P02``, … in the user-owned **Project Extension Rules**
section of ``references/code-hygiene-rules.reference.md``, outside the managed ``content`` fence. Before this,
the guidance was "projects add CH-21+" inside the fence, which collided with the framework's own CH-21…CH-31
and was overwritten by the next ``--update --merge``.

* :func:`migrate_project_rules` runs on that file's merge. Every ``### …`` rule section of the OLD managed region
  whose heading the new render does not carry (a project rule, including a local ``CH-29`` whose heading differs
  from the framework's) is moved, verbatim, into the Project Extension Rules section rather than dropped, and the
  caller gets a notice naming each one. A second run moves nothing.
* :func:`duplicate_rule_ids` and :func:`framework_ids_outside_fence` back the ``--doctor`` warnings.

Stdlib only.
"""

from __future__ import annotations

import re

#: The rendered file this module governs (relative to a team dir).
HYGIENE_REF_NAME = "code-hygiene-rules.reference.md"
_BEGIN = "<!-- AGENTTEAMS:BEGIN content"
_END = "<!-- AGENTTEAMS:END content -->"
_COMMENT_RE = re.compile(r"^<!--.*?-->", re.DOTALL | re.MULTILINE)  # block comments only (an inline `<!--` in prose is text)
_HEADING_RE = re.compile(r"^### (\S+)", re.MULTILINE)
_RULE_ID_RE = re.compile(r"^### (CH-[A-Za-z0-9]+)\b", re.MULTILINE)
_FRAMEWORK_ID_RE = re.compile(r"^CH-\d{1,2}$")
#: The user-owned section heading the template renders after the managed fence.
PROJECT_SECTION_HEADING = "## Project Extension Rules (CH-P01+)"
_PROJECT_SECTION = (
    f"\n{PROJECT_SECTION_HEADING}\n\n"
    "> ⚙️ **USER-EDITABLE** — this project's own hygiene rules, numbered `CH-P01`, `CH-P02`, … (a namespace reserved\n"
    "> for projects). This section lies outside the managed region, so `--update --merge` keeps it.\n"
)


def _managed_body(text: str) -> str | None:
    """The body of the ``content`` fence, or None when the file has none."""
    start = text.find(_BEGIN)
    end = text.find(_END, start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        return None
    return text[text.index("\n", start) + 1:end]


def _sections(body: str) -> list[tuple[str, str]]:
    """``(heading line, block)`` for every ``### …`` section of ``body`` (HTML comments ignored).

    A block runs from its heading to the next ``### ``/``## `` heading, a ``---`` rule line, or the end.
    """
    visible = _COMMENT_RE.sub("", body)
    out: list[tuple[str, str]] = []
    lines = visible.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        if lines[i].startswith("### "):
            j = i + 1
            while j < len(lines) and not (lines[j].startswith(("### ", "## ")) or lines[j].strip() == "---"):
                j += 1
            out.append((lines[i].rstrip("\n"), "".join(lines[i:j]).rstrip() + "\n"))
            i = j
        else:
            i += 1
    return out


def migrate_project_rules(old_text: str, merged_text: str) -> tuple[str, list[str]]:
    """Move rule sections the old managed region held but the new render lacks into the project section.

    Args:
        old_text: The on-disk file before the merge.
        merged_text: The merge result.

    Returns:
        ``(merged_text, notices)``: the merged text with each such section appended, verbatim, to the Project
        Extension Rules section (created after the managed fence when absent), and one notice naming the moved
        headings (empty when nothing moved).

    Raises:
        Nothing.
    """
    old_body = _managed_body(old_text)
    new_body = _managed_body(merged_text)
    if old_body is None or new_body is None:
        return merged_text, []
    kept = {h.strip() for h, _ in _sections(new_body)}
    outside = merged_text[merged_text.find(_END):]
    moved = [(h, block) for h, block in _sections(old_body)
             if h.strip() not in kept and h.strip() not in outside]
    if not moved:
        return merged_text, []
    out = merged_text.rstrip("\n") + "\n"
    if PROJECT_SECTION_HEADING not in out:
        out += _PROJECT_SECTION
    out += ("\n<!-- Moved here from the managed region by agentteams --update --merge: renumber these as CH-P01… -->\n"
            + "\n".join(block for _, block in moved))
    heads = ", ".join(h[4:].strip() for h, _ in moved)
    return out, [f"moved {len(moved)} project rule(s) out of the managed region into '{PROJECT_SECTION_HEADING}' "
                 f"(kept verbatim): {heads}. Renumber them CH-P01, CH-P02, …"]


def duplicate_rule_ids(text: str) -> list[tuple[str, int, int]]:
    """``(rule id, first line, repeat line)`` for every ``### CH-…`` heading used twice (comments ignored)."""
    seen: dict[str, int] = {}
    dups: list[tuple[str, int, int]] = []
    visible = _COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    for m in _RULE_ID_RE.finditer(visible):
        line = visible.count("\n", 0, m.start()) + 1
        if m.group(1) in seen:
            dups.append((m.group(1), seen[m.group(1)], line))
        else:
            seen[m.group(1)] = line
    return dups


def framework_ids_outside_fence(text: str) -> list[tuple[str, int]]:
    """``(rule id, line)`` for each framework-range id (``CH-1``…``CH-99``) used outside the managed fence."""
    end = text.find(_END)
    if end == -1:
        return []
    offset = text.count("\n", 0, end)
    tail = _COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text[end:])
    return [(m.group(1), offset + tail.count("\n", 0, m.start()) + 1) for m in _RULE_ID_RE.finditer(tail)
            if _FRAMEWORK_ID_RE.match(m.group(1))]


__all__ = ["HYGIENE_REF_NAME", "PROJECT_SECTION_HEADING", "duplicate_rule_ids", "framework_ids_outside_fence",
           "migrate_project_rules"]
