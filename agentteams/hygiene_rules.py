"""hygiene_rules.py — the code-hygiene rule catalogue's project rules: migration and id checks (IM item 26).

Projects number their own hygiene rules ``CH-P01``, ``CH-P02``, … in the user-owned **Project Extension Rules**
section of ``references/code-hygiene-rules.reference.md``, outside the managed ``content`` fence. Before this,
the guidance was "projects add CH-21+" inside the fence, which collided with the framework's own CH-21…CH-31
and was overwritten by the next ``--update --merge``.

* :func:`migrate_project_rules` runs on that file's merge. Every ``### CH-…`` rule section of the OLD managed
  region that is not a current framework rule (a project rule, including a local ``CH-29`` whose title differs
  from the framework's, or a framework rule a release renamed or removed) is moved, verbatim, into the Project
  Extension Rules section rather than dropped, and the caller gets a notice naming each one. Any other section the
  new render no longer carries is named in a notice too (not moved: it is not a rule). A second run moves nothing.
* :func:`duplicate_rule_ids` and :func:`framework_ids_outside_fence` back the ``--doctor`` warnings.

Stdlib only.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.fences import _extract_fenced_regions, _fence_body

#: The rendered file this module governs (relative to a team dir).
HYGIENE_REF_NAME = "code-hygiene-rules.reference.md"
_TEMPLATE = Path(__file__).parent / "templates" / "domain" / "code-hygiene-rules-reference.template.md"
_FENCE_ID = "content"
_END_RE = re.compile(r"^<!--\s*AGENTTEAMS:END\s+content\s*-->", re.MULTILINE)
_COMMENT_RE = re.compile(r"^<!--.*?-->", re.DOTALL | re.MULTILINE)  # block comments only (an inline `<!--` in prose is text)
_RULE_ID_RE = re.compile(r"^### (CH-[A-Za-z0-9]+)\b", re.MULTILINE)
_FRAMEWORK_ID_RE = re.compile(r"^CH-\d{1,2}$")
_PLACEHOLDER_RE = re.compile(r"\\\{[A-Z0-9_:]+\\\}")
#: The user-owned section heading the template renders after the managed fence.
PROJECT_SECTION_HEADING = "## Project Extension Rules (CH-P01+)"
_PROJECT_SECTION = (
    f"\n{PROJECT_SECTION_HEADING}\n\n"
    "> ⚙️ **USER-EDITABLE** — this project's own hygiene rules, numbered `CH-P01`, `CH-P02`, … (a namespace reserved\n"
    "> for projects). This section lies outside the managed region, so `--update --merge` keeps it.\n"
)


def _require_str(**values: object) -> None:
    for name, value in values.items():
        if not isinstance(value, str):
            raise TypeError(f"{name} must be str, got {type(value).__name__}")


def visible_text(text: str) -> str:
    """``text`` with each line-start HTML block comment blanked to its newlines (line numbers are kept).

    Args:
        text: Markdown text.

    Returns:
        The text a reader sees as headings: commented-out examples no longer count.

    Raises:
        TypeError: ``text`` is not a str.
    """
    _require_str(text=text)
    return _COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def _framework_heading_patterns() -> list[re.Pattern[str]]:
    """The packaged template's ``### CH-NN`` headings, each placeholder a wildcard (CH-03 names an output dir)."""
    if not _TEMPLATE.is_file():
        return []
    heads = re.findall(r"^### CH-\d\d — .*$", visible_text(_TEMPLATE.read_text(encoding="utf-8")), re.MULTILINE)
    return [re.compile(_PLACEHOLDER_RE.sub(".+?", re.escape(h.rstrip())) + r"\s*$") for h in heads]


def _managed_body(text: str) -> str | None:
    """The body of the ``content`` fence, or None when the file has none (or its fences do not parse)."""
    regions = _extract_fenced_regions(text)
    if isinstance(regions, str) or _FENCE_ID not in regions:
        return None
    return _fence_body(regions[_FENCE_ID])


def _sections(body: str) -> list[tuple[str, str]]:
    """``(heading line, verbatim block)`` for every ``## ``/``### `` section of ``body``.

    Headings inside an HTML block comment or a fenced code block do not count. A block runs, verbatim (comments
    included), to the next such heading or the end; a trailing ``---`` separator and blank lines are left out.
    """
    lines = body.splitlines(keepends=True)
    seen_lines = visible_text(body).splitlines(keepends=True)
    starts: list[int] = []
    in_code = False
    for i, seen in enumerate(seen_lines):
        if seen.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        elif not in_code and seen.startswith(("## ", "### ")):
            starts.append(i)
    out: list[tuple[str, str]] = []
    for k, i in enumerate(starts):
        block = lines[i:starts[k + 1] if k + 1 < len(starts) else len(lines)]
        while block and block[-1].strip() in ("", "---"):
            block.pop()
        out.append((lines[i].rstrip("\n"), "".join(block) + "\n"))
    return out


def _scan_flags(blocks: list[str]) -> list[str]:
    """High-severity findings of the injection/secret scan over the moved text (reported; the text is kept)."""
    from agentteams.scan import scan_content

    return sorted({f.message for b in blocks for f in scan_content(b, HYGIENE_REF_NAME) if f.severity == "high"})


def migrate_project_rules(old_text: str, merged_text: str) -> tuple[str, list[str]]:
    """Move rule sections the old managed region held but the new render lacks into the project section.

    Args:
        old_text: The on-disk file before the merge.
        merged_text: The merge result.

    Returns:
        ``(merged_text, notices)``: the merged text with each ``### CH-…`` section that is not a current framework
        rule appended, verbatim, to the Project Extension Rules section (created after the managed fence when
        absent), and notices naming what moved and any other old section the new render no longer carries
        (empty when there is nothing to say).

    Raises:
        TypeError: either argument is not a str.
    """
    _require_str(old_text=old_text, merged_text=merged_text)
    old_body, new_body = _managed_body(old_text), _managed_body(merged_text)
    end = _END_RE.search(merged_text)
    if old_body is None or new_body is None or end is None:
        return merged_text, []
    kept = {h.strip() for h, _ in _sections(new_body)}
    framework = _framework_heading_patterns()
    outside = {ln.strip() for ln in merged_text[end.end():].splitlines()}
    gone = [(h, b) for h, b in _sections(old_body)
            if h.strip() not in kept and h.strip() not in outside and not any(p.match(h) for p in framework)]
    moved = [(h, b) for h, b in gone if _RULE_ID_RE.match(h)]
    dropped = [h for h, _ in gone if not _RULE_ID_RE.match(h)]
    notices = []
    if dropped:
        notices.append(f"{len(dropped)} section(s) of the old managed region are not in the new render and were "
                       f"not moved (not CH- rules; a framework section a release renamed is expected here): "
                       f"{', '.join(h.lstrip('#').strip() for h in dropped)}")
    if not moved:
        return merged_text, notices
    out = merged_text.rstrip("\n") + "\n"
    if PROJECT_SECTION_HEADING not in outside:
        out += _PROJECT_SECTION
    out += ("\n<!-- Moved here from the managed region by agentteams --update --merge: review, then renumber "
            "project rules CH-P01… -->\n" + "\n".join(b for _, b in moved))
    flags = _scan_flags([b for _, b in moved])
    notices.insert(0, (
        f"moved {len(moved)} rule section(s) the new framework catalogue does not carry into "
        f"'{PROJECT_SECTION_HEADING}', verbatim and unreviewed; they now persist across merges: "
        f"{', '.join(h[4:].strip() for h, _ in moved)}. Each is a project rule (renumber it CH-P01, CH-P02, …) or a "
        "framework rule a release renamed or removed (delete it)."
        + (f" The security scan flagged the moved text: {'; '.join(flags)}" if flags else "")))
    return out, notices


def duplicate_rule_ids(text: str) -> list[tuple[str, int, int]]:
    """Every ``### CH-…`` heading id used twice (HTML block comments ignored).

    Args:
        text: A ``code-hygiene-rules.reference.md`` file's text.

    Returns:
        ``(rule id, first line, repeat line)`` per repeat, 1-based.

    Raises:
        TypeError: ``text`` is not a str.
    """
    seen: dict[str, int] = {}
    dups: list[tuple[str, int, int]] = []
    visible = visible_text(text)
    for m in _RULE_ID_RE.finditer(visible):
        line = visible.count("\n", 0, m.start()) + 1
        if m.group(1) in seen:
            dups.append((m.group(1), seen[m.group(1)], line))
        else:
            seen[m.group(1)] = line
    return dups


def framework_ids_outside_fence(text: str) -> list[tuple[str, int]]:
    """Framework-range ids (``CH-`` and one or two digits) used as rule headings after the managed fence.

    Args:
        text: A ``code-hygiene-rules.reference.md`` file's text.

    Returns:
        ``(rule id, line)`` per such heading, 1-based; empty when the file has no managed fence.

    Raises:
        TypeError: ``text`` is not a str.
    """
    _require_str(text=text)
    end = _END_RE.search(text)
    if end is None:
        return []
    visible = visible_text(text)
    return [(m.group(1), visible.count("\n", 0, m.start()) + 1) for m in _RULE_ID_RE.finditer(visible, end.end())
            if _FRAMEWORK_ID_RE.match(m.group(1))]


__all__ = ["HYGIENE_REF_NAME", "PROJECT_SECTION_HEADING", "duplicate_rule_ids", "framework_ids_outside_fence",
           "migrate_project_rules", "visible_text"]
