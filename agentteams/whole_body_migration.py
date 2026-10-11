"""whole_body_migration.py — retire a legacy whole-body ``content`` fence, and report fenced duplicates.

**The defect (2026-10-11 fleet audit).** A template with no fences is wrapped in one ``content`` fence at
emit time. Once the template gains named sections, the render stops being wrapped. The merge used to retire
the stale ``content`` block only when it was the file's *only* fence. Many deployed files also carry a
``memory_index_consultation`` fence (an earlier merge appended it), so the predicate failed. The merge then
added ``invariant_core``, ``output_format`` and the rest beside the old block, and every section appeared
twice, both copies inside fences: quality-auditor, tool-doc-researcher and conflict-resolution across 12
repositories, in ``.github/agents`` and in Goose recipes alike.

**The predicate.** Migrate when ``content`` is the only on-disk fence the render no longer has
(:func:`applies`). The other on-disk fences are all in the render, so each one maps one-to-one onto a fence
the ordinary merge updates in place. A file with a second orphaned fence is not migrated: its layout does
not match the render, and the duplicate report below names the problem instead.

**The method.** :func:`migrate` rewrites only the ``content`` block, then hands the file to the ordinary
merge, which updates every other fence in place, three-way merges the front matter and runs the twin pass.
The block is replaced by the render's body, from its first line to its last fence, with the fences the file
already carries cut out (they stay where they are). Everything outside the block is untouched: front matter,
the Goose recipe header, ``## Project-Specific Notes``. The earlier branch replaced the whole prefix,
front matter included, so a capability change or a project's own handoffs were overwritten without the
three-way merge ever seeing them.

**What sits inside the old block.** The fence says the text is template-owned, but a project may have
edited it. Each heading section of the old body (``#``/``##``, with its subsections) is matched to the render:

* a section whose heading the render keeps *unfenced*: the deployed copy is kept when it is not provably
  template text (template text means every line is in the render's copy, or the section equals a past
  template rendering in the lineage corpus). That section is project-owned from now on, exactly as it is in
  a freshly generated file;
* any other section: dropped when every line survives elsewhere in the result, or when it equals a past
  template rendering of its heading (lineage corpus). A ``USER-EDITABLE`` or ``Project-Specific`` region,
  and a section the template does not have at all, is carried over unfenced: keeping it duplicates nothing.
  Two kinds are not kept in the file: an unproven copy of a section the template now fences (keeping it
  recreates the duplicate this module exists to remove) and a section holding a managed marker (re-emitting
  a marker outside its fence would forge one). They are named in the notice, and their text is in the
  ``.lost.content.md`` sidecar.

Every kept, carried or unproven section is named in the migration notice, which ``fleet`` reads as REVIEW.

**Rule 12.** What the predicate newly permits is retiring ``content`` beside other fences. The drop decision
for text inside the block is bounded by the same proof standard as :mod:`agentteams.fence_twins` (the result
or the pinned lineage corpus), the full old body always goes to the sidecar, and a section that is not
proven is reported, never dropped silently.

Stdlib only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agentteams import fence_twins
from agentteams.unfenced import _FENCE_BEGIN_RE, _FENCE_END_RE
from agentteams.user_regions import HEADINGS as _USER_HEADINGS

#: The section id ``_normalize_generated_content`` uses when it wraps an entire unfenced body.
WHOLE_BODY_FENCE = "content"
#: Prefix of every duplicate-section notice. ``fleet`` files these as their own REVIEW class.
DUPLICATE_PREFIX = "duplicate section"

_SPLIT_RE = re.compile(r"^[ \t]*#{1,2}[ \t]+\S")
_HEADING_RE = re.compile(r"^#{2,6}[ \t]+\S")
_CODE_RE = re.compile(r"^[ \t]*(?:```|~~~)")
_FM_CLOSE_RE = re.compile(r"^---[ \t]*$")
_INSTRUCTIONS_RE = re.compile(r"^instructions:[ \t]*\|")
#: A managed-region marker of any kind (template fence, bridge, learned block), even quoted in prose.
_MARKER_RE = re.compile(r"AGENTTEAMS[-A-Z]*:(?:BEGIN|END)")
_TOML_DELIMS = ("'''", '"""')
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_USER_MARKER_RE = re.compile(r"^\s*>.*USER[- ]EDITABLE|^\s*#{1,6}\s.*USER[- ]EDITABLE|BEGIN USER[- ]EDITABLE")


@dataclass
class Migration:
    """The on-disk file with its ``content`` fence retired, ready for the ordinary merge.

    Attributes:
        content: The rewritten file.
        added: Section ids the render's body brought in (absent from the file before).
        old_body: The retired fence's body, for the ``.lost.content.md`` sidecar.
        kept_deployed: Headings whose deployed copy replaced the template's unfenced copy.
        carried: Headings of user regions carried out of the old fence.
        unproven: Headings dropped from the file although not provably template (sidecar only).
        retired: Headings dropped as a past template rendering of a section the template removed.
        other_fences: The other fences the file already carried, updated in place.
        bridge_blocks: How many ``AGENTTEAMS-BRIDGE`` blocks were carried out of the old fence.
    """

    content: str
    added: list[str] = field(default_factory=list)
    old_body: str = ""
    kept_deployed: list[str] = field(default_factory=list)
    carried: list[str] = field(default_factory=list)
    unproven: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    other_fences: list[str] = field(default_factory=list)
    bridge_blocks: int = 0


def applies(existing_regions: Mapping[str, str], new_regions: Mapping[str, str]) -> bool:
    """Whether the on-disk file carries a stale whole-body fence the render has split into sections.

    Args:
        existing_regions: Fenced regions found on disk.
        new_regions: Fenced regions in the fresh render.

    Returns:
        True when ``content`` is on disk, the render has named sections and no ``content``, and every
        other on-disk fence is still in the render.

    Raises:
        Nothing.
    """
    return (
        WHOLE_BODY_FENCE in existing_regions
        and WHOLE_BODY_FENCE not in new_regions
        and bool(new_regions)
        and set(existing_regions) - set(new_regions) == {WHOLE_BODY_FENCE}
    )


def _units(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    """Split *lines* into fence, text, continuation and space units.

    ``("text", heading, lines)`` starts at a ``#``/``##`` heading outside a code block and runs to the next
    one or to a fence marker; text before the first heading has the key ``""``. Text after a fence with no
    heading of its own continues the previous heading's section: ``("cont", that heading, lines)`` (a
    template can split one section around a fence). ``("fence", sid, lines)`` is a whole fenced block, and
    ``("space", "", lines)`` a blank-only run.
    """
    units: list[tuple[str, str, list[str]]] = []
    buf: list[str] = []
    key = ""
    cont = False
    in_code = False

    def flush() -> None:
        if buf:
            kind = ("cont" if cont else "text") if any(x.strip() for x in buf) else "space"
            units.append((kind, key, list(buf)))

    i = 0
    while i < len(lines):
        m = _FENCE_BEGIN_RE.search(lines[i])
        if m:
            flush()
            buf, in_code, cont = [], False, True
            j = i
            while j < len(lines) - 1 and not _FENCE_END_RE.search(lines[j]):
                j += 1
            units.append(("fence", m.group("sid"), lines[i:j + 1]))
            i = j + 1
            continue
        line = lines[i]
        if _CODE_RE.match(line):
            in_code = not in_code
        elif not in_code and _SPLIT_RE.match(line):
            flush()
            buf, key, cont = [], line.strip(), False
        buf.append(line)
        i += 1
    flush()
    return units


def _body_start(lines: list[str]) -> int:
    """Index of the first body line: after YAML front matter, or after a Goose ``instructions: |`` line."""
    for i, line in enumerate(lines):
        if _INSTRUCTIONS_RE.match(line):
            return i + 1
    if lines and lines[0].rstrip("\n") == "---":
        for i in range(1, len(lines)):
            if _FM_CLOSE_RE.match(lines[i].rstrip("\n")):
                return i + 1
    return 0


def _norm_lines(lines: list[str] | str) -> set[str]:
    """Whitespace-collapsed non-blank lines."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    return {fence_twins.norm(x) for x in lines if x.strip()}


def _covered(chunk: list[str], pool: set[str]) -> bool:
    """True when every non-blank line of *chunk*, outside HTML comments, is in *pool*.

    A comment (an old template's SECTION MANIFEST) instructs no agent, so a stale one is not a reason to
    keep the deployed copy. A managed marker is a comment too, but a chunk holding one is refused before
    this is asked.
    """
    text = _COMMENT_RE.sub("", "".join(chunk))
    return all(fence_twins.norm(x) in pool for x in text.splitlines() if x.strip())


def _corpus_match(chunk: list[str], heading: str, source: Any) -> bool:
    """True when *chunk* equals a past template rendering of *heading* (lineage corpus, today's values)."""
    if source is None or not heading:
        return False
    entries = fence_twins.load_corpus().get(getattr(source, "template", ""), {})
    want = fence_twins.norm("".join(chunk))
    placeholders = getattr(source, "placeholders", {}) or {}
    for raw_heading, rows in entries.items():
        if fence_twins.resolve_placeholders(raw_heading, placeholders).strip() != heading:
            continue
        if any(fence_twins.norm(fence_twins.resolve_placeholders(r.get("text", ""), placeholders)) == want
               for r in rows):
            return True
    return False


def _headings(text: str) -> tuple[set[str], set[str]]:
    """``(fenced, unfenced)`` heading lines of *text*, stripped (``#``-``######``)."""
    fenced: set[str] = set()
    unfenced: set[str] = set()
    depth = 0
    in_code = False
    for line in text.splitlines():
        if _FENCE_BEGIN_RE.search(line):
            depth, in_code = 1, False
            continue
        if _FENCE_END_RE.search(line):
            depth, in_code = 0, False
            continue
        if _CODE_RE.match(line):
            in_code = not in_code
            continue
        if not in_code and re.match(r"^[ \t]*#{1,6}[ \t]+\S", line):
            (fenced if depth else unfenced).add(line.strip())
    return fenced, unfenced


def _last_end(lines: list[str]) -> int:
    """Index of the last fence END marker line, or -1."""
    for i in range(len(lines) - 1, -1, -1):
        if _FENCE_END_RE.search(lines[i]):
            return i
    return -1


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _trim_blank_end(lines: list[str]) -> list[str]:
    out = list(lines)
    while out and not out[-1].strip():
        out.pop()
    return out


def _rebuild(units: list[tuple[str, str, list[str]]], skip: set[str], pool: dict[str, list[list[str]]],
             source: Any, mig: Migration, *, intro: bool) -> list[str]:
    """The render's units with on-disk fences cut out and edited unfenced sections taken from disk.

    Text before the first heading (key ``""``) is matched only when *intro* is set and it is the
    render's first unit: anywhere else an unheaded run is not the old body's preamble.
    """
    out: list[str] = []
    skip_space = False
    section: dict[str, set[str]] = {}          # heading -> the render's lines for the whole section
    for kind, key, lines in units:
        if kind in ("text", "cont"):
            section.setdefault(key, set()).update(_norm_lines(lines))
    used: dict[str, set[str]] = {}             # heading -> the deployed copy that replaced the render's
    for n, (kind, key, lines) in enumerate(units):
        if kind == "fence":
            if key in skip:
                skip_space = True
                continue
            out += lines
            mig.added.append(key)
        elif kind == "space":
            if not skip_space:
                out += lines
        elif kind == "cont":
            if key not in used or not _covered(lines, used[key]):
                out += lines
        else:
            deployed = pool.get(key, []) if key or (intro and n == 0) else []
            disk = deployed.pop(0) if deployed else None
            if disk is None or _covered(disk, section.get(key, set())) or _corpus_match(disk, key, source):
                out += lines
            else:
                out += disk[:-1] + [disk[-1] if disk[-1].endswith("\n") else disk[-1] + "\n"]
                used[key] = _norm_lines(disk)
                mig.kept_deployed.append(key or "(text before the first heading)")
        skip_space = skip_space and kind == "space"
    return out


def migrate(new_rendered: str, existing: str, existing_regions: Mapping[str, str], *,
            bridge_blocks: list[str] | None = None, twin_source: Any = None) -> Migration | None:
    """Retire the on-disk ``content`` fence in favour of the render's named sections.

    Args:
        new_rendered: The fresh render.
        existing: The on-disk file (its fences parse; :func:`applies` holds).
        existing_regions: The on-disk fenced regions.
        bridge_blocks: ``AGENTTEAMS-BRIDGE`` blocks inside the old fence to carry over verbatim.
        twin_source: The file's :class:`agentteams.fence_twins.TwinSource`, or None (limits proof to the
            result itself).

    Returns:
        The rewritten file and what happened to the old body, or None when the file's layout cannot be
        mapped safely (the render's indentation differs, or the render has no fence): the caller then
        falls back to the ordinary merge, which keeps ``content`` and reports the duplicates.

    Raises:
        Nothing.
    """
    d_lines = existing.splitlines(keepends=True)
    r_lines = new_rendered.splitlines(keepends=True)
    begin = next((i for i, x in enumerate(d_lines) if (m := _FENCE_BEGIN_RE.search(x))
                  and m.group("sid") == WHOLE_BODY_FENCE), None)
    end = next((i for i in range(begin or 0, len(d_lines)) if (m := _FENCE_END_RE.search(d_lines[i]))
                and m.group("sid") == WHOLE_BODY_FENCE), None) if begin is not None else None
    r_start, r_end = _body_start(r_lines), _last_end(r_lines)
    first_r = next((x for x in r_lines[r_start:] if _FENCE_BEGIN_RE.search(x)), None)
    if begin is None or end is None or r_end < r_start or first_r is None:
        return None
    if _indent(first_r) != _indent(d_lines[begin]):
        return None

    old_body = "".join(d_lines[begin + 1:end])
    mig = Migration(content=existing, old_body=old_body,
                    other_fences=sorted(set(existing_regions) - {WHOLE_BODY_FENCE}))
    body = old_body
    carried_bridge = [b for b in (bridge_blocks or []) if b in body]
    mig.bridge_blocks = len(carried_bridge)
    for block in carried_bridge:
        body = body.replace(block, "", 1)
    pool: dict[str, list[list[str]]] = {}
    order: list[tuple[str, list[str]]] = []
    for kind, key, lines in _units(body.splitlines(keepends=True)):
        if kind == "text":
            pool.setdefault(key, []).append(lines)
            order.append((key, lines))

    # The title section ("# <Agent> — <Project>") is matched by position, not by text: a changed project
    # name changes the heading, and matching by text would keep the old title AND add the new one.
    render_units = _units(r_lines[r_start:r_end + 1])
    r_title = next((key for kind, key, _ in render_units if kind == "text" and key.startswith("# ")), None)
    d_title = order[0][0] if order and order[0][0].startswith("# ") else None
    if r_title and d_title and r_title != d_title and r_title not in pool and pool.get(d_title):
        pool[r_title] = [pool[d_title].pop(0)]
    before, after = d_lines[:begin], d_lines[end + 1:]
    tail_blank = not "".join(d_lines[_last_end(d_lines) + 1:]).strip()
    skip = set(mig.other_fences)
    section = _trim_blank_end(_rebuild(_units(r_lines[r_start:r_end + 1]), skip, pool, twin_source, mig,
                                       intro=True))
    tail: list[str] = []
    if tail_blank and "".join(r_lines[r_end + 1:]).strip():
        # The file ends at its last fence: the render's own tail (## Project-Specific Notes) is new.
        tail = _rebuild(_units(r_lines[r_end + 1:]), skip, pool, twin_source, mig, intro=False)
        after = _trim_blank_end(after)
    section += ["\n"] + carried_bridge if carried_bridge else []

    def assemble(extra: list[str]) -> str:
        mid = section + (["\n"] + extra if extra else [])
        text = "".join(before + mid + (["\n"] if after and after[0].strip() else []) + after)
        if tail:
            text = text.rstrip("\n") + "\n" + "".join(tail)
        return text if text.endswith("\n") else text + "\n"

    fenced_r, unfenced_r = _headings(new_rendered)
    leftovers = [(key, lines) for key, lines in order if any(lines is c for c in pool.get(key, []))]
    result_lines = _norm_lines(assemble([]))
    carried: list[str] = []
    for key, lines in leftovers:
        label = key or "(text before the first heading)"
        if _covered(lines, result_lines):
            continue
        text = "".join(lines)
        user_region = key in _USER_HEADINGS or any(_USER_MARKER_RE.search(x) for x in lines)
        if _MARKER_RE.search(text) or any(d in text for d in _TOML_DELIMS):
            mig.unproven.append(label)      # never re-emit a managed marker outside its fence
        elif user_region:
            carried += _trim_blank_end(lines) + ["\n"]
            mig.carried.append(label)
        elif key in fenced_r:
            # The template now fences this section: keeping the old copy recreates the duplicate.
            if not _corpus_match(lines, key, twin_source):
                mig.unproven.append(label)
        elif key and _corpus_match(lines, key, twin_source):
            mig.retired.append(label)
        elif key in unfenced_r and fence_twins.norm(key) in result_lines:
            mig.unproven.append(label)      # the result already has this heading: a second copy would duplicate it
        else:
            carried += _trim_blank_end(lines) + ["\n"]
            (mig.kept_deployed if key in unfenced_r else mig.carried).append(label)
    mig.content = assemble(_trim_blank_end(carried))
    return mig


def migration_notice(mig: Migration, *, has_tail: bool) -> str:
    """The ``fence 'content': ...`` notice for a migration (a shrink-class notice: REVIEW, with a sidecar).

    Args:
        mig: The migration.
        has_tail: Whether content outside the old fence was carried over unchanged.

    Returns:
        One notice line.

    Raises:
        Nothing.
    """
    parts = [
        f"fence '{WHOLE_BODY_FENCE}': this file predates its template being split into named sections; "
        f"the whole-body fence was replaced by {', '.join(sorted(mig.added)) or 'the render'}"
    ]
    if mig.other_fences:
        parts.append(f" (the fence(s) it already carried, {', '.join(mig.other_fences)}, were updated in place)")
    parts.append("; the template-owned text inside it was replaced")
    if mig.kept_deployed:
        parts.append("; kept the deployed copy of " + ", ".join(repr(h) for h in mig.kept_deployed)
                     + " (not provably template text; the template leaves it unfenced, so it is project-owned "
                       "now — compare with the template's copy)")
    if mig.bridge_blocks:
        parts.append(f"; carried {mig.bridge_blocks} AGENTTEAMS-BRIDGE block(s) over unchanged")
    if mig.carried:
        parts.append("; carried " + ", ".join(repr(h) for h in mig.carried)
                     + " out of the fence unchanged (a user region, or a section the template does not have and "
                       "that is not provably template text: it is unfenced and project-owned now — review it)")
    if mig.retired:
        parts.append("; dropped " + ", ".join(repr(h) for h in mig.retired)
                     + " (a past template rendering of a section the template retired)")
    if mig.unproven:
        parts.append("; NOT KEPT, not provably template text: " + ", ".join(repr(h) for h in mig.unproven)
                     + " — review the old body in the .lost sidecar and move any real additions to "
                       "## Project-Specific Notes")
    parts.append("; content outside it was carried over unchanged." if has_tail else ".")
    return "".join(parts)


def duplicate_notices(merged: str, added_sids: list[str]) -> list[str]:
    """Report headings that appear twice in a merged file, saying accurately where each copy sits.

    Three shapes:

    * one copy inside a fence and one outside (the pre-fencing residue): reported on every merge;
    * every copy inside a fence, spread over two or more fences (a stale fence kept beside the one that
      replaced it, such as a legacy ``content`` block beside ``invariant_core``): reported on every merge;
    * every copy outside a fence: reported only when the merge added a fence, since a project may repeat
      its own headings.

    A heading repeated inside one fence is the template's own structure and is never reported. A Goose
    recipe is read through its dedented ``instructions:`` block.

    Args:
        merged: The merged file content.
        added_sids: section_ids the merge inserted that were absent from the on-disk file.

    Returns:
        Notices, each starting with :data:`DUPLICATE_PREFIX`. Empty when nothing collides.

    Raises:
        Nothing.
    """
    text = fence_twins.dedented_view(merged)
    where: dict[str, list[str | None]] = {}
    sid: str | None = None
    in_code = False
    for line in text.splitlines():
        m = _FENCE_BEGIN_RE.search(line)
        if m:
            sid, in_code = m.group("sid"), False
            continue
        if _FENCE_END_RE.search(line):
            sid, in_code = None, False
            continue
        if _CODE_RE.match(line):
            in_code = not in_code
            continue
        if not in_code and _HEADING_RE.match(line):
            where.setdefault(line.strip(), []).append(sid)
    added = ", ".join(sorted(added_sids))
    out: list[str] = []
    for heading, sids in where.items():
        n, inside = len(sids), [s for s in sids if s is not None]
        if n < 2:
            continue
        fences = sorted(set(inside))
        if inside and len(inside) < n:
            out.append(
                f"{DUPLICATE_PREFIX} {heading!r}: appears {n}x, inside a fence ({', '.join(fences)}) and "
                + (f"outside one after adding {added} — the deployed file already carried this section unfenced. "
                   "Delete the unfenced copy; the fenced one is now template-owned." if added_sids else
                   "outside one — the unfenced copy predates the fence. Move any real additions to "
                   "## Project-Specific Notes, then delete the unfenced copy."))
        elif len(fences) > 1:
            stale = (" The legacy whole-body 'content' fence was kept beside the named sections that replaced "
                     "it; a merge with the coexisting-fence migration retires it."
                     if WHOLE_BODY_FENCE in fences else "")
            out.append(
                f"{DUPLICATE_PREFIX} {heading!r}: appears {n}x, every copy inside a fence ({', '.join(fences)}) — "
                f"two template-owned blocks carry the same section, so the agent reads two versions of it.{stale} "
                "Move any real additions to ## Project-Specific Notes, then delete the stale block.")
        elif not inside and added_sids:
            out.append(
                f"{DUPLICATE_PREFIX} {heading!r}: appears {n}x outside every fence after adding {added} — "
                "check that neither copy is a stale pre-fencing copy of the added section.")
    return out
