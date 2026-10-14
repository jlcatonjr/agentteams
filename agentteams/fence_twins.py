"""fence_twins.py — resolve the unfenced pre-fencing copy ("twin") of a section a template now fences.

**The defect.** A team rendered before its template fenced ``## Accuracy Rules`` carries that
section unfenced. The next ``--update --merge`` adds the fenced block and keeps the unfenced copy,
because everything outside a fence is preserved verbatim. The file then holds the section twice:
researchteam's ``technical-validator`` carried the CH-01..10 table beside the template's TV-01..07,
and two ``## Invariant Core`` headings (2026-10-10, 5 researchteam-family and 13 Visual-Knowledge
repositories).

**What authorises a drop.** Only proof that the twin is template text, never a heuristic about it:
after whitespace is collapsed (nothing else is normalised), the twin's text must equal

* the body of the fence it duplicates, as that fence stands in the merged file (an identical copy
  survives inside the fence, so nothing is lost); or
* a section text some earlier agentteams template emitted UNFENCED under that heading, from the
  shipped lineage corpus (``agentteams/data/template_lineage.json``, generated from git history by
  ``scripts/build_template_lineage.py`` and regeneration-checked), resolved with today's placeholder
  values. This is only consulted when the current template section, resolved the same way, equals
  the fresh fenced body — when a framework adapter or enrichment transforms the section, a
  historical render is not comparable and the twin is kept.

**Only the twin's head is ever dropped.** A twin can outlive the fence's extent: navigator's
pre-fencing ``## Invariant Core`` carried ``### Project Structure`` and ``### Workstream → Source File
Mapping``, which today's template emits *after* the ``invariant_core`` fence, unfenced. The merge
never re-adds unfenced template text, so dropping the whole span lost those blocks (2026-10-13
fleet audit: MarketForge, Tracers goose, sc_collectors and vk-address-enrichment ``.claude``,
ProjectRepositories). The twin is therefore cut at its first subheading the fresh render carries
outside every fence (:func:`twin_head`); the equality proof runs on that head (each past rendering
is cut the same way), and the tail is always kept: moved to just below the fence when the twin sat
directly above it, which is where the template puts it, otherwise left in place.

Anything else is kept, with a ``twin kept`` notice that ``fleet`` turns into REVIEW. Every error
here is in the keeping direction: a placeholder value that changed, a renamed placeholder, a
one-word edit, an extra table row (researchteam's hand-written CH-08..10) all fail the equality.

**Acknowledged twins.** An operator who decides to keep a twin records it in the team's
``references/fence-twin-acks.csv`` (``agentteams.fence_twin_acks``). The row binds to
:func:`ack_digest` (file, heading, whitespace-collapsed text). A matching row turns the kept notice
into ``twin kept (acknowledged)``, which ``fleet`` does not count as REVIEW. It is consulted only
after the drop decision is final, and only to choose the notice for a twin already being kept, so
it can never cause a drop. Once the text changes, the row no longer matches and REVIEW returns.

**Refusals (kept, never dropped).** The heading leads more than one fence, or appears unfenced more
than once; the twin has no following heading of its level or higher and no fence marker after it
(it would run to the end of the file or of the Goose ``instructions:`` block); the twin's span holds
an ``AGENTTEAMS`` region marker of any kind (even quoted), ``USER-EDITABLE``, a ``Project-Specific`` heading, or a TOML
string delimiter; the fence is not in the current render; or dropping is disabled for the run
(no backup dir).

**Rule 12.** The head cut newly permits dropping a head whose tail is not template text (an edited
``### Project Structure`` used to keep the whole twin, and with it a second ``## Invariant Core``).
What it deletes is still proven template text and the tail it exempts is kept, never dropped. Without
a fresh render (``fresh=""``) the twin cannot be bounded, so it is kept. An operator acknowledgement
of a twin a head cut would newly split keeps it whole, and a tail whose heading the file already
carries elsewhere stays in place with a ``duplicate section`` notice (REVIEW). The corpus is deletion
authority: text added to it becomes deletable from every deployed repository. It is therefore
generated from git only, checked by regeneration, and pinned in
``references/enforcement-integrity.json`` together with this module. Placeholder substitution is
done here rather than through ``render.py``, so the equality test itself is pinned. Its INPUTS are not
all pinned: the placeholder values and template path (``cli/render_pipeline.twin_sources``), the
drop permission (``emit.py``), the current templates read by the adapter guard, and the Goose helpers
(``user_regions._reindent``, ``goose_recipe_merge.is_goose_recipe``). Tampering with those can only
change which text counts as "template", the same exposure as editing the unpinned renderer itself.

Stdlib only.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.fence_twin_acks import DIGEST_HEX as _ACK_DIGEST_HEX
from agentteams.fence_twin_acks import LEDGER_REL as ACK_LEDGER_REL
from agentteams.fence_twin_acks import TwinAck, ledger_row
from agentteams.fences import _extract_fenced_regions, _fence_body, _write_lost_fence_sidecars
from agentteams.unfenced import (
    CONSTRAINT_BEARING_RE,
    _FENCE_BEGIN_RE,
    _FENCE_END_RE,
    is_trackable_constraint_line,
)
from agentteams.user_regions import _reindent

_PACKAGE_DIR = Path(__file__).resolve().parent
#: The shipped lineage corpus (see the module docstring).
CORPUS_PATH = _PACKAGE_DIR / "data" / "template_lineage.json"
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+\S")
_TWIN_HEADING_RE = re.compile(r"^#{2,6}[ \t]+\S")
_CODE_FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~)")
_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
_INSTRUCTIONS_RE = re.compile(r"^instructions:[ \t]*\|[^\n]*\n", re.MULTILINE)
#: Text whose presence in a twin's span refuses a drop: a user region or a TOML string delimiter (a span
#: crossing one would corrupt a Codex agent) ...
_REFUSE_IN_SPAN = ("USER-EDITABLE", "Project-Specific", "'''", '"""')
#: ... or any managed-region marker (template fence, bridge, learned block), even one quoted in prose.
#: Matching the marker rather than the bare word keeps a twin that merely names an env var
#: (``AGENTTEAMS_WAIVER_SIGNING_KEY``) resolvable.
_MARKER_RE = re.compile(r"AGENTTEAMS[-A-Z]*:(?:BEGIN|END)")
#: Notice prefixes ``fleet`` parses. Kept stable: a kept twin is a REVIEW reason.
KEPT_PREFIX = "twin kept (not provably template)"
DROPPED_PREFIX = "twin dropped"
#: A twin kept on an operator's acknowledgement (``fleet`` lists it under ``twins_acknowledged``, without REVIEW).
ACKED_PREFIX = "twin kept (acknowledged)"
#: Marks a drop that took a rule line the current template no longer carries (``fleet`` -> REVIEW).
RETIRED_MARK = "retired by template"
#: Prefix of every duplicate-section notice (``fleet`` files these as their own REVIEW class). Single-sourced
#: here; :mod:`agentteams.whole_body_migration` re-exports it.
DUPLICATE_PREFIX = "duplicate section"


@dataclass(frozen=True)
class TwinSource:
    """Where a rendered file came from: enough to re-resolve historical template sections.

    Attributes:
        template: Template path relative to the templates dir (the manifest's ``template``).
        placeholders: The placeholder values the current render used for this file.
        rel_path: The file's output-relative path ("" when unknown: no twin can be acknowledged).
        acks: The operator's acknowledgement rows for this file (``agentteams.fence_twin_acks``).
    """

    template: str
    placeholders: Mapping[str, str] = field(default_factory=dict)
    rel_path: str = ""
    acks: tuple[TwinAck, ...] = ()


@dataclass
class TwinOutcome:
    """What :func:`resolve` decided for one merged file.

    Attributes:
        content: The merged content with every dropped twin removed.
        dropped: section_id -> the exact twin text removed (for the ``.lost`` sidecar).
        kept: Headings of twins left in place (acknowledged ones included).
        acknowledged: The subset of ``kept`` an operator acknowledgement matched (no REVIEW).
        notices: One human-readable notice per twin (dropped or kept).
        handled: Headings this pass examined, so the generic duplicate report can skip them.
    """

    content: str
    dropped: dict[str, str] = field(default_factory=dict)
    kept: list[str] = field(default_factory=list)
    acknowledged: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    handled: set[str] = field(default_factory=set)


def norm(text: str) -> str:
    """Collapse whitespace — the only normalisation applied before an equality test.

    Args:
        text: Any text.

    Returns:
        *text* with every whitespace run replaced by one space and the ends stripped.
    """
    return " ".join(text.split())


def resolve_placeholders(text: str, placeholders: Mapping[str, str]) -> str:
    """Substitute ``{UPPER_SNAKE}`` tokens that have a value; leave every other token intact.

    The body-text half of ``render.resolve_placeholders`` (template sections never carry front
    matter), kept here so the drop decision does not depend on an unpinned module.

    Args:
        text: Raw template text.
        placeholders: Placeholder values.

    Returns:
        The resolved text. An unknown token stays literal, so it cannot match rendered text.
    """
    return _PLACEHOLDER_RE.sub(lambda m: placeholders.get(m.group(1), m.group(0)), text)


def _line_states(lines: list[str]) -> list[tuple[str, bool]]:
    """Classify each line as ``marker``, ``fenced`` or ``open``, with whether it sits in a code block.

    Code-block state resets at every fence marker, so an unbalanced code fence inside a managed
    region cannot hide headings outside it.
    """
    states: list[tuple[str, bool]] = []
    depth = 0
    in_code = False
    for line in lines:
        if _FENCE_BEGIN_RE.search(line) or _FENCE_END_RE.search(line):
            depth = max(0, depth + (1 if _FENCE_BEGIN_RE.search(line) else -1))
            in_code = False
            states.append(("marker", False))
            continue
        if _CODE_FENCE_RE.match(line):
            states.append(("fenced" if depth else "open", True))
            in_code = not in_code
            continue
        states.append(("fenced" if depth else "open", in_code))
    return states


def unfenced_section(text: str, heading: str, *, allow_eof: bool = False) -> tuple[int, int] | str:
    """Locate the single unfenced occurrence of *heading* and bound its section.

    The section runs from the heading line to the first later line that is a fence marker or a
    heading of the same or a higher level, outside any code block.

    Args:
        text: File (or dedented Goose instructions) content.
        heading: The heading line, stripped (``"## Accuracy Rules"``).
        allow_eof: Bound at the end of *text* when nothing follows. Only the corpus builder passes
            True — a template's last section ends where the template does; a deployed file's does not.

    Returns:
        ``(start, end)`` character offsets, or a refusal reason: ``"absent"``, ``"appears N times
        unfenced"`` or ``"no following heading or fence marker"``.

    Raises:
        Nothing.
    """
    lines = text.splitlines(keepends=True)
    states = _line_states(lines)
    hits = [i for i, line in enumerate(lines)
            if states[i] == ("open", False) and line.strip() == heading]
    if not hits:
        return "absent"
    if len(hits) > 1:
        return f"appears {len(hits)} times unfenced"
    start = hits[0]
    level = len(heading) - len(heading.lstrip("#"))
    end = None
    for j in range(start + 1, len(lines)):
        kind, in_code = states[j]
        if kind == "marker":
            end = j
            break
        m = _HEADING_RE.match(lines[j])
        if kind == "open" and not in_code and m and len(m.group(1)) <= level:
            end = j
            break
    if end is None:
        if not allow_eof:
            return "no following heading or fence marker"
        end = len(lines)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets[start], offsets[end]


def fence_leading_headings(text: str) -> dict[str, list[str]]:
    """Map each heading that leads a fence body to the section_ids it leads.

    Args:
        text: Content carrying AGENTTEAMS fences.

    Returns:
        ``{heading: [sid, ...]}``; empty when the fences do not parse.

    Raises:
        Nothing.
    """
    regions = _extract_fenced_regions(text)
    out: dict[str, list[str]] = {}
    if not isinstance(regions, dict):
        return out
    for sid, block in regions.items():
        for line in _fence_body(block).splitlines():
            if line.strip():
                if _TWIN_HEADING_RE.match(line.strip()):
                    out.setdefault(line.strip(), []).append(sid)
                break
    return out


@functools.lru_cache(maxsize=4)
def load_corpus(path: Path = CORPUS_PATH) -> dict:
    """Load the lineage corpus's ``templates`` table; empty when it is absent or unreadable.

    Args:
        path: The corpus file.

    Returns:
        ``{template: {raw heading: [{"text", "commit", "path"}, ...]}}``. An empty table only ever
        keeps twins, so a missing corpus fails safe.

    Raises:
        Nothing.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    templates = payload.get("templates") if isinstance(payload, dict) else None
    return templates if isinstance(templates, dict) else {}


def _template_section(source: TwinSource, sid: str, templates_dir: Path) -> str | None:
    """The current template's body for *sid*, resolved; None when unavailable."""
    try:
        raw = (templates_dir / source.template).read_text(encoding="utf-8")
    except OSError:
        return None
    regions = _extract_fenced_regions(raw)
    if not isinstance(regions, dict) or sid not in regions:
        return None
    return resolve_placeholders(_fence_body(regions[sid]), source.placeholders)


def _lineage_match(twin: str, heading: str, sid: str, new_body: str, source: TwinSource | None,
                   corpus: Mapping, templates_dir: Path,
                   outside: frozenset[str] = frozenset()) -> tuple[str | None, str]:
    """Match *twin* against the lineage corpus. Returns ``(match label or None, why not)``.

    Each past rendering is cut at its first *outside* subheading, exactly as the twin was, so the
    comparison is prefix against prefix.
    """
    if source is None:
        return None, "no template source for this file"
    current = _template_section(source, sid, templates_dir)
    if current is None or norm(current) != norm(new_body):
        return None, "the adapter or enrichment transforms this section, so past renders are not comparable"
    entries = corpus.get(source.template, {})
    for raw_heading in sorted(entries):
        if resolve_placeholders(raw_heading, source.placeholders).strip() != heading:
            continue
        for entry in entries[raw_heading]:
            past = resolve_placeholders(entry.get("text", ""), source.placeholders)
            if norm(twin_head(past, outside)) == norm(twin):
                return f"template {entry.get('path', '?')}@{str(entry.get('commit', '?'))[:10]}", ""
    return None, "matches no past template rendering of this section"


def outside_headings(rendered: str) -> frozenset[str]:
    """The subheadings a fresh render carries OUTSIDE every fence (whitespace-collapsed).

    A legacy twin can run past the point where its template now ends the fence: navigator's
    pre-fencing ``## Invariant Core`` held ``### Project Structure`` and ``### Workstream → Source
    File Mapping``, which today's template emits after the ``invariant_core`` fence, unfenced. Those
    headings bound what a twin drop may take (:func:`twin_head`).

    Args:
        rendered: The fresh render (a Goose recipe is read through its dedented instructions).

    Returns:
        Each ``##``..``######`` heading line outside any fence and code block, whitespace-collapsed.

    Raises:
        Nothing.
    """
    lines = dedented_view(rendered).splitlines()
    return frozenset(norm(line) for line, state in zip(lines, _line_states(lines))
                     if state == ("open", False) and _TWIN_HEADING_RE.match(line))


def twin_head(section: str, outside: frozenset[str]) -> str:
    """The part of *section* before its first subheading the template now emits outside the fence.

    The first line (the twin's own heading) is never a cut point; headings inside a code block are
    not headings.

    Args:
        section: A twin's text, or a past template rendering of the same heading.
        outside: :func:`outside_headings` of the fresh render.

    Returns:
        The leading part of *section*; all of it when no such subheading occurs.

    Raises:
        Nothing.
    """
    if not outside:
        return section
    lines = section.splitlines(keepends=True)
    in_code = False
    taken = len(lines[0]) if lines else 0
    for line in lines[1:]:
        if _CODE_FENCE_RE.match(line):
            in_code = not in_code
        elif not in_code and _TWIN_HEADING_RE.match(line) and norm(line) in outside:
            return section[:taken]
        taken += len(line)
    return section


def _retired_rules(twin: str, fresh: str) -> list[str]:
    """Constraint-bearing lines of a dropped twin that the fresh render no longer carries."""
    haystack = norm(fresh)
    return [norm(line) for line in twin.splitlines()
            if is_trackable_constraint_line(line) and CONSTRAINT_BEARING_RE.search(line)
            and norm(line) not in haystack]


def ack_digest(rel_path: str, heading: str, twin: str) -> str:
    """The digest an acknowledgement binds to: the file, the heading and the twin's text.

    Args:
        rel_path: Output-relative path of the file.
        heading: The twin's heading line, stripped.
        twin: The twin's text (heading included); only whitespace is collapsed.

    Returns:
        The first 16 hex characters of the sha256 of the three, NUL-separated.

    Raises:
        Nothing.
    """
    payload = "\0".join((rel_path, heading, norm(twin)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_ACK_DIGEST_HEX]


def _keep_notice(heading: str, sid: str, why: str, text: str, source: TwinSource | None, out: TwinOutcome) -> str:
    """The notice for a twin left in place, after checking the operator's acknowledgements.

    Never changes what is kept: the caller has already decided to keep the twin. A matching
    acknowledgement only swaps :data:`KEPT_PREFIX` (``fleet`` REVIEW) for :data:`ACKED_PREFIX`.
    """
    span = unfenced_section(text, heading) if text and source is not None and source.rel_path else None
    if not isinstance(span, tuple):
        return (f"{KEPT_PREFIX}: {heading!r} beside fence '{sid}' — {why}; resolve by hand (move real "
                "additions to ## Project-Specific Notes, then delete the unfenced copy)")
    digest = ack_digest(source.rel_path, heading, text[span[0]:span[1]])
    on_record = [a for a in source.acks if a.file == source.rel_path and a.heading == heading]
    match = next((a for a in on_record if a.digest == digest), None)
    if match is not None:
        out.acknowledged.append(heading)
        return (f"{ACKED_PREFIX}: {heading!r} beside fence '{sid}' — {why}; kept as acknowledged in "
                f"{ACK_LEDGER_REL} (digest {digest}): {match.reason}")
    stale = (f"; the acknowledgement on record ({', '.join(a.digest for a in on_record)}) no longer "
             "matches: the twin changed since it was reviewed") if on_record else ""
    return (f"{KEPT_PREFIX}: {heading!r} beside fence '{sid}' — {why}{stale}; resolve by hand (move real "
            "additions to ## Project-Specific Notes, then delete the unfenced copy), or, to keep it on "
            f"purpose, add this row to {ACK_LEDGER_REL}: {ledger_row(source.rel_path, heading, digest)}")


def _bounded_twins(text: str, leads: Mapping[str, list[str]],
                   new_regions: Mapping[str, str]) -> dict[str, tuple[str, tuple[int, int] | None, str]]:
    """``{heading: (sid, span or None, refusal reason or "")}`` for every heading with an unfenced copy."""
    found: dict[str, tuple[str, tuple[int, int] | None, str]] = {}
    for heading in sorted(leads):
        span = unfenced_section(text, heading)
        if span == "absent":
            continue
        sids = leads[heading]
        sid = sids[0]
        if len(sids) > 1:
            found[heading] = (sid, None, f"the heading leads {len(sids)} fences ({', '.join(sorted(sids))})")
        elif isinstance(span, str):
            found[heading] = (sid, None, span)
        elif sid not in new_regions:
            found[heading] = (sid, None, f"fence '{sid}' is not in the current render")
        elif (any(token in text[span[0]:span[1]] for token in _REFUSE_IN_SPAN)
              or _MARKER_RE.search(text[span[0]:span[1]])):
            found[heading] = (sid, None, "its span holds a managed marker, a user-editable region or a string delimiter")
        else:
            found[heading] = (sid, span, "")
    # Nested twins (an unfenced "### B" inside an unfenced "## A" whose fences are separate): dropping
    # either could take part of the other. Refuse both rather than order the deletions.
    spans = {h: v[1] for h, v in found.items() if v[1] is not None}
    for heading, span in spans.items():
        if any(o != heading and span[0] < other[1] and other[0] < span[1] for o, other in spans.items()):
            found[heading] = (found[heading][0], None, "it overlaps the unfenced copy of another fenced section")
    return found


def _resolve_text(text: str, new_regions: Mapping[str, str], source: TwinSource | None,
                  allow_drop: bool, corpus: Mapping, templates_dir: Path, fresh: str) -> TwinOutcome:
    """Resolve the twins of *text* (already dedented for a Goose recipe)."""
    out = TwinOutcome(content=text)
    leads = fence_leading_headings(text)
    regions = _extract_fenced_regions(text)
    if not leads or not isinstance(regions, dict):
        return out
    outside = outside_headings(fresh)
    edits: list[tuple[int, int, str]] = []
    for heading, (sid, span, reason) in _bounded_twins(text, leads, new_regions).items():
        out.handled.add(heading)
        if span is None:
            out.kept.append(heading)
            out.notices.append(_keep_notice(heading, sid, reason, text, source, out))
            continue
        # Only the twin's head is a candidate: from the first subheading the template now emits
        # outside the fence, the span is template text the merge never re-adds, so it stays.
        twin = twin_head(text[span[0]:span[1]], outside)
        cut = span[0] + len(twin)
        if norm(twin) == norm(_fence_body(regions[sid])):
            match, why = "an identical copy kept inside the fence", ""
        else:
            match, why = _lineage_match(twin, heading, sid, _fence_body(new_regions[sid]), source,
                                        corpus, templates_dir, outside)
        if match is not None:
            if not allow_drop:
                why = f"it matches {match}, but dropping is disabled (no backup dir)"
            elif not fresh:
                why = f"it matches {match}, but there is no fresh render to bound it with"
            elif cut < span[1] and _acknowledged(text, heading, span, source):
                why = f"its head matches {match}, but an operator acknowledgement keeps the whole twin"
        if match is None or why:
            out.kept.append(heading)
            out.notices.append(_keep_notice(heading, sid, why, text, source, out))
            continue
        edits.append((span[0], cut, ""))
        out.dropped[sid] = twin
        retired = _retired_rules(twin, fresh)
        tail_note, tail_edits = _keep_tail(text, (span[0], cut, span[1]), sid, edits)
        edits += tail_edits
        out.notices.append(
            f"{DROPPED_PREFIX}: {heading!r} (unfenced copy beside fence '{sid}') matched {match}; its text is "
            f"saved to the .lost.unfenced-{sid}.md sidecar in the backup dir"
            + (f"; {RETIRED_MARK}: {'; '.join(retired)}" if retired else "") + tail_note)
        out.notices += _duplicate_tail_notices(text, (span[0], cut, span[1]))
    for start, end, insert in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        out.content = out.content[:start] + insert + out.content[end:]
    return out


def _acknowledged(text: str, heading: str, span: tuple[int, int], source: TwinSource | None) -> bool:
    """Whether an operator acknowledgement on record matches the whole twin at *span*."""
    if source is None or not source.rel_path:
        return False
    digest = ack_digest(source.rel_path, heading, text[span[0]:span[1]])
    return any(a.file == source.rel_path and a.heading == heading and a.digest == digest for a in source.acks)


def _elsewhere(text: str, bounds: tuple[int, int, int]) -> list[str]:
    """Headings of the tail ``text[cut:end]`` that also appear unfenced outside the twin's span."""
    start, cut, end = bounds
    tail = {norm(line) for line in text[cut:end].splitlines() if _TWIN_HEADING_RE.match(line)}
    rest = text[:start] + text[end:]
    return sorted(tail & outside_headings(rest))


def _duplicate_tail_notices(text: str, bounds: tuple[int, int, int]) -> list[str]:
    """A ``duplicate section`` notice per tail heading the file already carries elsewhere (REVIEW)."""
    return [f"{DUPLICATE_PREFIX} {h!r}: kept from a dropped twin although the file already carries it outside "
            "every fence — compare the two copies, move any real additions to ## Project-Specific Notes, then "
            "delete the stale one" for h in _elsewhere(text, bounds)]


def _keep_tail(text: str, bounds: tuple[int, int, int], sid: str,
               edits: list[tuple[int, int, str]]) -> tuple[str, list[tuple[int, int, str]]]:
    """Plan how a dropped twin's tail (``text[cut:end]``) is kept; return ``(notice suffix, edits)``.

    The tail is moved to just after fence *sid* when it ends where that fence begins (the layout a
    merge leaves when it added the fence below the twin), which is where the template puts it. It
    stays in place when it does not, when the move point falls inside one of *edits* (read, never
    changed), or when the file already carries one of its headings elsewhere (the duplicate is
    flagged by :func:`_duplicate_tail_notices` rather than moved next to its twin copy).
    """
    _start, cut, end = bounds
    tail = text[cut:end]
    if not tail.strip():
        return "", []
    headings = [line.strip() for line in tail.splitlines() if _TWIN_HEADING_RE.match(line)]
    kept = (f"; kept {', '.join(repr(h) for h in headings)} from the twin (the template carries "
            f"{'them' if len(headings) > 1 else 'it'} outside the fence)")
    lines = text[end:].splitlines(keepends=True)
    begin = _FENCE_BEGIN_RE.search(lines[0]) if lines else None
    close = None
    if begin is not None and begin.group("sid") == sid and not _elsewhere(text, bounds):
        ends = (i for i, line in enumerate(lines) if (m := _FENCE_END_RE.search(line)) and m.group("sid") == sid)
        close = next(ends, None)
    if close is not None and lines[close].endswith("\n"):
        at = end + sum(len(line) for line in lines[:close + 1])
        if not any(s <= at <= e for s, e, _ in edits if e > s):
            moved = [(cut, end, ""), (at, at, "\n" + tail.rstrip("\n") + "\n")]
            return f"{kept}, moved below fence '{sid}' where the template puts it", moved
    return f"{kept}, left in place (it now sits under the preceding section's heading)", []


def _instructions_block(text: str) -> tuple[int, int, str] | None:
    """``(start, end, indent)`` of a Goose recipe's ``instructions: |`` body, or None."""
    from agentteams.frameworks.goose_recipe_merge import is_goose_recipe

    if not is_goose_recipe(text):
        return None
    m = _INSTRUCTIONS_RE.search(text)
    if m is None:
        return None
    start = m.end()
    nxt = re.search(r"^(?![ \t]|$|#)", text[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(text)
    first = re.search(r"^([ \t]+)\S", text[start:end], re.MULTILINE)
    if first is None:
        return None
    return start, end, first.group(1)


def _indent(text: str, indent: str) -> str:
    """Re-indent column-0 *text* into a YAML block (blank lines kept as they are)."""
    return "".join(indent + line if line.strip() else line for line in text.splitlines(keepends=True))


def dedented_view(text: str) -> str:
    """Return a Goose recipe's ``instructions:`` body at column 0, or *text* itself otherwise.

    Args:
        text: File content.

    Returns:
        The dedented instructions block of a Goose recipe; *text* unchanged for any other file, or
        when a line of the block is under-indented.

    Raises:
        Nothing.
    """
    block = _instructions_block(text)
    if block is None:
        return text
    dedented = _reindent(text[block[0]:block[1]], block[2], "")
    return text if dedented is None else dedented


def resolve(merged: str, new_regions: Mapping[str, str], source: TwinSource | None = None, *,
            allow_drop: bool = False, fresh: str = "", corpus: Mapping | None = None,
            templates_dir: Path | None = None) -> TwinOutcome:
    """Drop each unfenced twin that is provably template text; keep and flag every other one.

    Runs on every merge, not only when a fence was just added, so a file already carrying both
    copies is examined again. A Goose recipe is resolved inside its dedented ``instructions:``
    block and re-indented; an under-indented line there leaves the file untouched.

    Args:
        merged: The merged file content.
        new_regions: The fresh render's fenced regions (section_id -> block).
        source: The file's template and placeholder values; None limits proof to the fenced copy.
        allow_drop: False keeps every twin (the run has no backup dir to recover one from).
        fresh: The fresh render: it bounds each twin (:func:`outside_headings`) and feeds the "retired by
            template" rule listing. Empty keeps every twin.
        corpus: The lineage table (defaults to the shipped corpus).
        templates_dir: Where current templates live (defaults to the package's).

    Returns:
        A :class:`TwinOutcome`; its ``content`` equals *merged* when nothing was dropped.

    Raises:
        Nothing.
    """
    corpus = load_corpus() if corpus is None else corpus
    templates_dir = templates_dir or _TEMPLATES_DIR
    block = _instructions_block(merged)
    if block is None:
        return _resolve_text(merged, new_regions, source, allow_drop, corpus, templates_dir, fresh)
    start, end, indent = block
    dedented = _reindent(merged[start:end], indent, "")
    if dedented is None:
        return TwinOutcome(content=merged)
    out = _resolve_text(dedented, new_regions, source, allow_drop, corpus, templates_dir, fresh)
    if out.dropped:
        out.dropped = {sid: _indent(text, indent) for sid, text in out.dropped.items()}
        out.content = merged[:start] + _indent(out.content, indent) + merged[end:]
    else:
        out.content = merged
    return out


def persist_dropped(rel_path: str, dropped: Mapping[str, str], backup_path: Path | None) -> list[str]:
    """Write each dropped twin to ``<backup>/<rel_path>.lost.unfenced-<sid>.md``.

    Args:
        rel_path: Output-relative path of the merged file.
        dropped: section_id -> dropped twin text.
        backup_path: The run's backup dir (None only in a dry run: nothing is dropped without one).

    Returns:
        One notice per twin: its recovery path, or a warning when the sidecar could not be written
        (the run's full-file backup still holds the text).

    Raises:
        Nothing.
    """
    if not dropped or backup_path is None:
        return []
    written = _write_lost_fence_sidecars(backup_path, rel_path, {f"unfenced-{s}": t for s, t in dropped.items()})
    return [f"{rel_path}: {DROPPED_PREFIX} (fence '{sid}') — recovery: {written[f'unfenced-{sid}']}"
            if f"unfenced-{sid}" in written else
            f"{rel_path}: {DROPPED_PREFIX} (fence '{sid}') — the .lost sidecar could not be written; recover "
            "it from the run's full-file backup"
            for sid in sorted(dropped)]


__all__ = [
    "ACKED_PREFIX",
    "CORPUS_PATH",
    "DROPPED_PREFIX",
    "KEPT_PREFIX",
    "RETIRED_MARK",
    "TwinOutcome",
    "TwinSource",
    "ack_digest",
    "dedented_view",
    "fence_leading_headings",
    "load_corpus",
    "norm",
    "outside_headings",
    "persist_dropped",
    "resolve",
    "resolve_placeholders",
    "twin_head",
    "unfenced_section",
]
