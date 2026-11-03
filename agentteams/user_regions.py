"""user_regions.py — the user-editable regions of generated files, and carrying them across ``--overwrite``.

Two regions are user-owned and outside every ``AGENTTEAMS`` fence:

* ``## Project-Specific Notes``, at the end of every agent persona: Markdown, a Codex ``developer_instructions``
  body, and (from P5a) a Goose recipe's ``instructions: |`` block. A fresh Copilot ``.agent.md`` render puts it
  before the body's first named-section fence instead (baseAgent P4: GitHub truncates a body past 30,000 characters);
* ``## Project-Specific Rules``, in the rendered instruction files (``CLAUDE.md``, ``.claude/CLAUDE.md``,
  ``AGENTS.md``, ``.github/copilot-instructions.md``), and in ``AGENTS.override.md`` / ``.goosehints`` when
  :mod:`agentteams.rules_projection` adds one there.

``--merge`` keeps them because it keeps everything outside a fence. ``--overwrite`` used to write the new
render straight over the file, so a team's own rules and duties were lost. mathAgents lost its orchestrator's
D39 duties and two instruction-file rules that way. :func:`carry_regions` carries each region from the file on
disk into the new render. It re-indents for a YAML block scalar, and refuses rather than drop a region the new
render has no place for, unless the region holds only generated text (the USER-EDITABLE note and a projected
block, which the next projection writes again).

:func:`insert_missing_regions` adds the empty Rules region (and its SECTION MANIFEST row) to an instruction file
that predates it on ``--update --merge``, which otherwise never takes unfenced text from the template.

Carried text is instruction-bearing (C-4: an injected region would survive the reset), so the caller prints
every carried region and offers ``--discard-user-regions``.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tomllib

HEADINGS = ("## Project-Specific Notes", "## Project-Specific Rules")
_NOTES_HEADING, _RULES_HEADING = HEADINGS

# A learned-block marker *line* (:mod:`agentteams.learned_blocks` matches whole stripped lines). The word in
# prose is not a marker: a region that only mentions it is carried. Whitespace inside the comment is optional
# here so that a near-miss marker is refused too.
_LEARNED_MARKER_LINE_RE = re.compile(r"^[ \t]*<!--\s*AGENTTEAMS-LEARNED\b", re.MULTILINE)


def find_region(text: str, heading: str) -> tuple[int, int, str] | None:
    """Locate a user region.

    The region starts at the heading line. It ends at the first of: a later ``AGENTTEAMS:BEGIN`` fence line, a
    TOML string terminator line (``'''``), a line indented less than the heading (the end of a YAML block
    scalar), or the end of the text.

    Args:
        text: The file content.
        heading: One of :data:`HEADINGS`.

    Returns:
        ``(start, end, indent)``, or ``None`` when the heading is absent.

    Raises:
        Nothing.
    """
    m = re.search(rf"^([ \t]*){re.escape(heading)}[ \t]*$", text, re.MULTILINE)
    if m is None:
        return None
    indent = m.group(1)
    start = m.start()
    pos = m.end()
    end = len(text)
    for line in re.finditer(r"^.*$", text[pos:], re.MULTILINE):
        raw = line.group(0)
        stripped = raw.strip()
        if not stripped:
            continue
        lead = raw[: len(raw) - len(raw.lstrip())]
        if (stripped.startswith("<!-- AGENTTEAMS") or stripped == "'''"
                or (indent and len(lead) < len(indent))):
            end = pos + line.start()
            break
    return start, end, indent


def reindent(region: str, old_indent: str, new_indent: str) -> str | None:
    """Move *region* from *old_indent* to *new_indent*; None when a line is under-indented (it would end the
    YAML block scalar early, so it can't be carried safely)."""
    if old_indent == new_indent:
        return region
    out = []
    for line in region.splitlines(keepends=True):
        if not line.strip():
            out.append(line)
            continue
        if not line.startswith(old_indent):
            return None
        out.append(new_indent + line[len(old_indent):])
    return "".join(out)


_reindent = reindent  # older private name (fence_twins)


def skip_banner(lines: list[str], start: int = 0) -> int:
    """Index of the first line after a region's generated USER-EDITABLE note.

    Skips blank lines from *start*, then the note's blockquote paragraph when it is the generated one (its first
    line names ``USER-EDITABLE``). An operator's own blockquote is not skipped.

    Args:
        lines: The region's lines, heading excluded.
        start: Where to start.

    Returns:
        The index of the first operator line (``len(lines)`` when there is none).

    Raises:
        Nothing.
    """
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith(">") and "USER-EDITABLE" in lines[i]:
        while i < len(lines) and lines[i].lstrip().startswith(">"):
            i += 1
    return i


def _inside_fence(text: str, idx: int) -> bool:
    before = text[:idx]
    return before.count("<!-- AGENTTEAMS:BEGIN") > before.count("<!-- AGENTTEAMS:END")


def _next_heading(text: str, after: int, level: str) -> str | None:
    """The next heading line of *level* (e.g. ``## ``) after *after*: the next template-owned section."""
    for line in text[after:].splitlines():
        if line.startswith(level) and not line.startswith(level + "#"):
            return line
    return None


def carry_regions(old: str, new: str, *, toml: bool = False) -> tuple[str, list[tuple[str, int, str]], list[str]]:
    """Carry each user region of *old* (the file on disk) into *new* (the fresh render).

    Args:
        old: The current file content.
        new: The freshly rendered content.
        toml: The file is a Codex TOML agent. A region containing a TOML string delimiter is refused,
            and the carried result must still parse, so carried text can never close the string and
            inject keys.

    Returns:
        ``(content, carried, problems)``:
        - *content*: *new* with *old*'s regions in place;
        - *carried*: ``(heading, line_count, cut)`` for each region whose text differed; *cut* names the
          template heading the old region was cut at (and the lines dropped there), else ``""``;
        - *problems*: one message per region that couldn't be carried, either because the new render has no
          such heading or because a line is under-indented. The caller must not overwrite the file then.

    Raises:
        Nothing.
    """
    carried: list[tuple[str, int, str]] = []
    problems: list[str] = []
    content = new
    for heading in HEADINGS:
        old_span = find_region(old, heading)
        if old_span is None:
            continue
        o_start, o_end, o_indent = old_span
        cut_at = None
        new_span = find_region(content, heading)
        if new_span is not None:
            # End both regions where the new render's next template-owned section of the same level begins
            # (e.g. an unfenced "## Code Style" after the Rules), so stale template text is never carried.
            following = _next_heading(content, new_span[0] + 1, o_indent + "## ")
            if following is not None:
                # Whole-line matches only. More than one means the user's own text repeats the template's next
                # heading: cutting at either would silently drop text, so refuse instead.
                hits = [m.start() for m in re.finditer(rf"^{re.escape(following)}[ \t]*$", old[o_start:o_end],
                                                        re.MULTILINE)]
                if len(hits) > 1:
                    problems.append(f"'{heading}' contains the template heading '{following.strip()}' more than "
                                    "once; it can't be split from the template section safely")
                    continue
                if hits:
                    cut_at = (following.strip(), old[o_start + hits[0]:o_end])
                    o_end = o_start + hits[0]
        region = old[o_start:o_end]
        if (_inside_fence(old, o_start) or "<!-- AGENTTEAMS" in region
                or _LEARNED_MARKER_LINE_RE.search(region)):
            problems.append(f"'{heading}' sits inside a fence or contains an AGENTTEAMS marker; it can't be "
                            "carried safely")
            continue
        if toml and ("'''" in region or '"""' in region):
            problems.append(f"'{heading}' contains a TOML string delimiter; it can't be carried safely")
            continue
        if new_span is None:
            # A region the render doesn't declare that holds only generated text (the USER-EDITABLE note and a
            # rules_projection block, which the next projection writes again) has nothing of the operator's to keep.
            body = _PROJECTED_BLOCK_RE.sub("", region).splitlines(keepends=True)[1:]
            if not any(ln.strip() for ln in body[skip_banner(body):]):
                continue
            problems.append(f"the new render has no '{heading}' region to carry the existing one into")
            continue
        n_start, n_end, n_indent = new_span
        following = _next_heading(content, n_start + 1, n_indent + "## ")
        if following is not None:
            m = re.search(rf"^{re.escape(following)}[ \t]*$", content[n_start:n_end], re.MULTILINE)
            if m:
                n_end = n_start + m.start()
        moved = reindent(region, o_indent, n_indent)
        if moved is None:
            problems.append(f"'{heading}' has a line indented less than its heading; it can't be carried safely")
            continue
        if moved.rstrip() == content[n_start:n_end].rstrip():
            continue
        if not moved.endswith("\n"):
            moved += "\n"
        content = content[:n_start] + moved + content[n_end:]
        dropped = cut_at[1].strip().count("\n") + 1 if cut_at and cut_at[1].strip() else 0
        carried.append((heading, moved.count("\n"), f"{cut_at[0]} ({dropped} lines)" if dropped else ""))
    if toml and carried:
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            return new, [], [f"the carried result is not valid TOML ({exc}); refused"]
    return content, carried, problems


#: A generated block of :mod:`agentteams.rules_projection` (a projected copy or a notes pointer).
_PROJECTED_BLOCK_RE = re.compile(r"^[ \t]*<!-- (PROJECTED-RULES|PROJECT-RULES-POINTER):BEGIN .*?^[ \t]*<!-- \1:END -->[ \t]*\n?",
                                 re.MULTILINE | re.DOTALL)

#: Instruction files that declare ``## Project-Specific Rules`` (basename match).
INSTRUCTION_FILES = frozenset({"copilot-instructions.md", "CLAUDE.md", "AGENTS.md"})
_REGION_SECTION_IDS = {_NOTES_HEADING: "project_specific_notes", _RULES_HEADING: "project_specific_rules"}
_MANIFEST_RE = re.compile(r"SECTION MANIFEST.*?-->", re.DOTALL)


def _manifest_row(text: str, section_id: str) -> re.Match[str] | None:
    m = _MANIFEST_RE.search(text)
    if m is None:
        return None
    return re.compile(rf"^\|\s*{re.escape(section_id)}\s*\|.*\n", re.MULTILINE).search(text, m.start(), m.end())


def _insert_manifest_row(fresh: str, merged: str, section_id: str) -> str:
    """Add *section_id*'s SECTION MANIFEST row from *fresh* to *merged*'s manifest when it lacks one.

    The row goes after the row that precedes it in *fresh*'s manifest, when *merged* has that row, else at the
    end of the table. A file without a manifest is left alone.
    """
    row = _manifest_row(fresh, section_id)
    target = _MANIFEST_RE.search(merged)
    if row is None or target is None or _manifest_row(merged, section_id) is not None:
        return merged
    fresh_manifest = _MANIFEST_RE.search(fresh)
    previous = list(re.finditer(r"^\|\s*([A-Za-z0-9_]+)\s*\|.*\n", fresh[fresh_manifest.start():row.start()],
                                re.MULTILINE))
    at = None
    if previous:
        anchor = _manifest_row(merged, previous[-1].group(1))
        at = anchor.end() if anchor is not None else None
    if at is None:
        rows = list(re.finditer(r"^\|.*\|[ \t]*\n", merged[target.start():target.end()], re.MULTILINE))
        if not rows:
            return merged
        at = target.start() + rows[-1].end()
    return merged[:at] + row.group(0) + merged[at:]


def _has_heading_like(text: str, heading: str) -> bool:
    """Whether *text* has a heading spelled like *heading* at any level, case or trailing punctuation.

    An operator who wrote ``## Project-specific rules:`` already has the region; inserting the template's
    would duplicate it.
    """
    words = r"[\s_-]+".join(re.escape(w) for w in re.split(r"[\s-]+", heading.lstrip("# ")))
    return re.search(rf"^[ \t]*#{{1,6}}[ \t]+{words}\b", text, re.MULTILINE | re.IGNORECASE) is not None


def _anchor_lines(text: str) -> list[str]:
    """Heading and fence-marker lines: the structure a region can be positioned against."""
    return [ln for ln in text.splitlines() if ln.startswith("## ") or ln.startswith("<!-- AGENTTEAMS:BEGIN ")]


def _anchor_position(merged: str, anchor: str) -> int | None:
    """Where *anchor* starts in *merged*: exactly one whole-line match outside every fence, else None."""
    hits = [m.start() for m in re.finditer(rf"^{re.escape(anchor)}$", merged, re.MULTILINE)]
    return hits[0] if len(hits) == 1 and not _inside_fence(merged, hits[0]) else None


def insert_missing_regions(rel_path: str, fresh: str, merged: str) -> tuple[str, list[str]]:
    """Give an instruction file each user region its template declares but the file lacks (``--update --merge``).

    ``--merge`` keeps everything outside a fence, but takes nothing unfenced from the template, so a file
    rendered before the template gained a region never gets it. The region is inserted empty (the template's
    heading and USER-EDITABLE note, taken from *fresh*) before the first of the template's later headings or
    fence lines that the file has, else at the end; its SECTION MANIFEST row is added too. A region already
    present anywhere in the file, at any indent, is never touched.

    Args:
        rel_path: Output path relative to the agents directory.
        fresh: The fresh render.
        merged: The merged content about to be written.

    Returns:
        ``(content, notices)``.

    Raises:
        Nothing.
    """
    if rel_path.rsplit("/", 1)[-1] not in INSTRUCTION_FILES:
        return merged, []
    notices: list[str] = []
    for heading in HEADINGS:
        span = find_region(fresh, heading)
        if span is None or _has_heading_like(merged, heading):
            continue
        if span[2]:  # an indented region lives in a YAML block scalar, never in an instruction file
            continue
        start, end, _ = span
        following = _next_heading(fresh, start + 1, "## ")
        if following is not None:
            m = re.search(rf"^{re.escape(following)}[ \t]*$", fresh[start:end], re.MULTILINE)
            if m:
                end = start + m.start()
        region = fresh[start:end].rstrip("\n") + "\n"
        separator = "---\n\n" if fresh[:start].rstrip().endswith("\n---") else ""
        at = next((p for p in (_anchor_position(merged, ln) for ln in _anchor_lines(fresh[end:]))
                   if p is not None), None)
        if at is not None:
            head = merged[:at]
            if separator and head.rstrip().endswith("\n---"):
                separator = ""
            content = head + separator + region + "\n" + merged[at:]
        else:
            if separator and merged.rstrip().endswith("\n---"):
                separator = ""
            content = merged.rstrip("\n") + "\n\n" + separator + region
        section_id = _REGION_SECTION_IDS[heading]
        merged = _insert_manifest_row(fresh, content, section_id)
        notices.append(f"added the template's empty '{heading}' region (USER-EDITABLE, kept across --update "
                       "--merge); the file predates it")
    return merged, notices


_RECIPE_RE = re.compile(r'^version:\s*["\']?1\.0\.0["\']?\s*$', re.MULTILINE)
_INSTRUCTIONS_RE = re.compile(r"^instructions:[ \t]*\|[-+]?[ \t]*$", re.MULTILINE)


def ensure_recipe_notes(recipe: str, notes_section: str) -> str:
    """Give a Goose recipe a ``## Project-Specific Notes`` region at the end of its ``instructions: |`` block.

    Args:
        recipe: Recipe YAML.
        notes_section: :data:`agentteams.fences.PROJECT_NOTES_SECTION`.

    Returns:
        The recipe with the region, or unchanged when it already has one or has no block-scalar
        ``instructions``.

    Raises:
        Nothing.
    """
    if not _RECIPE_RE.search(recipe) or _NOTES_HEADING in recipe:
        return recipe
    m = _INSTRUCTIONS_RE.search(recipe)
    if m is None:
        return recipe
    indent = None
    pos = m.end() + 1
    end = len(recipe)
    for line in re.finditer(r"^.*$", recipe[pos:], re.MULTILINE):
        raw = line.group(0)
        if not raw.strip():
            continue
        lead = len(raw) - len(raw.lstrip())
        if indent is None:
            indent = lead
            if indent == 0:
                return recipe
            continue
        if lead < indent:
            end = pos + line.start()
            break
    if indent is None:
        return recipe
    block = "".join((" " * indent + ln) if ln.strip() else ln for ln in notes_section.splitlines(keepends=True))
    head = recipe[:end].rstrip("\n") + "\n"
    return head + block + ("\n" if end < len(recipe) else "") + recipe[end:]


def overwrite_carry(rel_path: str, existing: str, content: str, *, notices: list[str],
                    errors: list[str] | None = None, dry_run: bool = False) -> tuple[str, list[str]]:
    """The ``--overwrite`` step of ``emit``: carry *existing*'s regions into *content* and report each one.

    Args:
        rel_path: The file's output-relative path (for messages).
        existing: The file on disk.
        content: The fresh render.
        notices: Receives one notice per carried region (printed to stderr too, unless *dry_run*).
        errors: Receives one error per region that can't be carried (the caller must skip the file).
        dry_run: Report "would carry" / "would refuse" instead.

    Returns:
        ``(content, problems)``.

    Raises:
        Nothing.
    """
    carried_content, carried, problems = carry_regions(existing, content, toml=rel_path.endswith(".toml"))
    for heading, lines, cut in carried:
        span = find_region(carried_content, heading)
        digest = hashlib.sha256(carried_content[span[0]:span[1]].encode()).hexdigest()[:12] if span else "?"
        if dry_run:
            notices.append(f"{rel_path}: would carry user region '{heading}' ({lines} lines, sha256 {digest}) "
                           "across --overwrite")
            continue
        note = (f"{rel_path}: carried user region '{heading}' ({lines} lines, sha256 {digest}) across "
                "--overwrite; it is instruction-bearing text, so review it (or re-run with "
                "--discard-user-regions to drop it)")
        if cut:
            note += (f". The region was cut at the template heading {cut}: that text was replaced by the "
                     "fresh template section; if any of it was yours, restore it from the backup")
        notices.append(note)
        print(f"  ↺  {note}", file=sys.stderr)
    for problem in problems:
        if dry_run:
            notices.append(f"{rel_path}: would refuse to overwrite: {problem}")
        elif errors is not None:
            errors.append(f"{rel_path}: not overwritten: {problem} (re-run with --discard-user-regions to drop "
                          "the region)")
    return carried_content, problems
