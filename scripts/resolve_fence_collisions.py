#!/usr/bin/env python3
"""Remove a deployed file's pre-fencing copy of a section its template now fences.

**The problem.** When a template fences a section that a deployed team predates, the merge *adds*
the fenced block while the team's own unfenced copy of that section is preserved unconditionally.
Two copies, one stale. ``fences._detect_duplicate_sections`` reports each occurrence; this script
resolves the subset that can be resolved safely. Measured against this repository's ``.claude/``
team: 40 collisions across 19 files, ``## Invariant Core`` duplicated 12 times.

**Why this is a script and not a CLI flag.** It deletes content. A destructive helper should not
sit one typo away from a routine command, and this one runs once per team, not per build.

**What authorises a delete.** Not the heuristic that sized the job — "short, single-occurrence,
unfenced" estimated how much was tractable, and an estimate must never be what permits a write.
The proof is per collision: the deployed unfenced section must equal the incoming fenced body
once whitespace is collapsed. Only whitespace; no case folding, no punctuation stripping. Two
sections differing in any word are unequal and the collision is reported instead.

Four ways this could destroy content, and what stops each:

1. A section with no following heading runs to end-of-file, so deleting it would also take
   ``## Project-Specific Notes`` — the region ``_split_at_last_fence_end`` exists to protect,
   destroyed by the tool cleaning up after it. Resolution normally requires a following heading
   of the same or higher level. The one exception is *proved, not assumed*: when the text from
   the trailing heading to EOF equals the body of a fence already in the same deployed file,
   removing it deletes nothing that does not survive inside that fence
   (``_trailing_duplicates_a_deployed_fence``). Anything after the duplicate — an operator note
   carries no heading, so the walk still calls the section trailing — breaks the equality and
   the section is reported instead.
2. A heading appearing more than once on disk makes the boundaries ambiguous. Exactly one
   unfenced occurrence is required.
3. A file with no fresh render is skipped.
4. Whitespace-only normalisation, so no real difference can be normalised away.

Usage::

    python scripts/resolve_fence_collisions.py --brief <brief.json> --output <agents-dir>
    python scripts/resolve_fence_collisions.py --brief ... --output ... --apply
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import hashlib  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402

from agentteams.backup import backup_output_dir  # noqa: E402
from agentteams.fences import (  # noqa: E402
    _extract_fenced_regions,
    _fence_body,
    _merge_fenced_content,
    unfenced_lines,
)

_HEADING_RE = re.compile(r"^(#{2,6})\s+\S")


def _norm(text: str) -> str:
    """Collapse whitespace. The only normalisation applied before an equality test."""
    return " ".join(text.split())


def _run_pipeline(brief: Path, out_dir: Path, framework: str) -> None:
    """Render a team into *out_dir* for *framework*.

    The framework is explicit and required. An earlier draft reused the snapshot-regen helper,
    which does not forward it and therefore defaulted to ``copilot-vscode``: that emits
    ``<slug>.agent.md`` while a Claude team on disk is ``<slug>.md``, so every agent file failed
    to match its render and the run reported 7 collisions instead of 40. It did not error — it
    under-reported, which on a tool that decides what to delete is the worse failure.
    """
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from test_integration import _run_pipeline as _rp  # type: ignore[import-not-found]

    _rp(brief, out_dir, framework=framework)


def _file_is_pristine(agents_dir: Path, deployed: Path, ref: str = "HEAD") -> bool:
    """True when *deployed* is byte-identical to what agentteams last emitted.

    The second authorisation, and a stronger one than the equality proof. If a file still hashes
    to its build-log entry, every byte in it was written by this tool — so an unfenced copy of a
    section the template now fences is *necessarily* the stale pre-fencing version, whatever it
    says. No project edit can be hiding in it, because the file contains no project edit at all.
    Same reasoning `_merge_front_matter` uses to decide when a template value may be applied,
    applied to sections instead of keys.

    Falls back to the file's state at *ref* because a prior equality-proof run can itself modify
    files (deletions only) and thereby invalidate their working-tree hash. ``HEAD`` is the default
    and is wrong once that run has been committed — hence the ref is an operator-supplied argument
    rather than a hardcoded ``HEAD~1``, which would be brittle against any other history shape.
    The comparison is always against the **build log**, never against the ref's content, so a
    project edit that was merely committed still fails.

    A stale build log makes this conservative, never permissive: a hash that does not match
    declines the resolution.
    """
    log = agents_dir / "references" / "build-log.json"
    if not log.is_file():
        return False
    try:
        hashes = json.loads(log.read_text(encoding="utf-8")).get("file_hashes", {})
    except (OSError, json.JSONDecodeError):
        return False
    rel = deployed.relative_to(agents_dir).as_posix()
    recorded = hashes.get(rel)
    if not recorded:
        return False

    def _matches(blob: str) -> bool:
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        return digest.startswith(recorded) or recorded.startswith(digest[: len(recorded)])

    if _matches(deployed.read_text(encoding="utf-8")):
        return True
    head = subprocess.run(
        ["git", "show", f"{ref}:{deployed.as_posix()}"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    return head.returncode == 0 and _matches(head.stdout)


_PROJECT_NOTES = "## Project-Specific Notes"


_FENCE_BEGIN_COUNT_RE = re.compile(r"AGENTTEAMS:BEGIN\s+([A-Za-z0-9_-]+)")


def _fenced_char_spans(text: str) -> list[tuple[int, int]]:
    """Character spans covered by AGENTTEAMS fences, markers included.

    Used to decide whether a given occurrence of a heading sits inside a fence. Computed
    from the markers directly rather than via `_extract_fenced_regions`, so it still works
    on a file whose fences do not parse — where a refusal is wanted, but an exception is
    not.
    """
    spans: list[tuple[int, int]] = []
    opens: list[int] = []
    for m in re.finditer(r"^<!--\s*AGENTTEAMS:(BEGIN|END)\s+\S+.*?-->\s*$", text, re.MULTILINE):
        if m.group(1) == "BEGIN":
            opens.append(m.start())
        elif opens:
            spans.append((opens.pop(), m.end()))
    return spans


def _duplicate_headings_in_file(text: str) -> list[str]:
    """Headings that appear BOTH inside a fenced region and outside one.

    The merge-notice path (`duplicate_section_notices`) only fires while a fence is being
    ADDED. After `--update --merge` has added it, the pre-existing unfenced twin is still
    in the file but generates no notice — so the resolver reported 0 collisions against a
    tree carrying 23 duplicate headings in 17 files. The merge creates the duplicate and
    blinds the detector for it in one step.

    Requiring one copy inside a fence and one outside is deliberately narrower than
    "appears twice": two unfenced copies are a template or authoring problem, not a
    fencing collision, and nothing here is authorised to resolve those.

    Args:
        text: The deployed file's content.

    Returns:
        Sorted headings meeting both conditions.
    """
    unfenced = {ln.strip() for ln in unfenced_lines(text) if ln.strip().startswith("#")}
    if not unfenced:
        return []
    regions = _extract_fenced_regions(text)
    if not isinstance(regions, dict):
        return []
    fenced_headings: set[str] = set()
    for body in regions.values():
        fenced_headings |= {
            ln.strip() for ln in body.splitlines() if ln.strip().startswith("#")
        }
    return sorted(unfenced & fenced_headings)


def _unfenced_starts(text: str, heading: str) -> list[int]:
    """Offsets of *heading* that lie outside every fenced region.

    Single-sourced because the same bug was written twice: counting every regex match, fenced
    ones included, so a heading with a fenced twin always looked "duplicated". That refused all
    20 real collisions in this repo's own team through `_unfenced_section_span`, and — fixed
    there but not here — went on to refuse the six trailing ones through `_trailing_span`,
    including under `--trust-provenance`, which was supposed to be their escape hatch.
    """
    fenced_spans = _fenced_char_spans(text)
    return [
        m.start()
        for m in re.finditer(rf"^{re.escape(heading)}\s*$", text, re.MULTILINE)
        if not any(lo <= m.start() < hi for lo, hi in fenced_spans)
    ]


def _trailing_span(text: str, heading: str) -> tuple[int, int] | str:
    """Span of a trailing section, bounded at end-of-file. Only safe under the caller's guards."""
    starts = _unfenced_starts(text, heading)
    if len(starts) != 1:
        return "duplicated"
    return starts[0], len(text)


def _deployed_fence_carrying(text: str, heading: str) -> str | None:
    """The body of the fence **in this deployed file** that carries *heading*, if exactly one does.

    The survivor of a removal. Every deletion this script performs is justified by "a fenced
    copy remains" — and until 2026-08-03 nothing checked that the fenced copy was on disk.
    ``incoming`` is read from the *fresh render*, so a template fence proves nothing about the
    deployed file, and two separate losses followed from that:

    * 2026-08-01 — ``security.md`` went from 363 lines to 32 on the strength of an
      ``invariant_core`` fence it had never received.
    * 2026-08-03 — ``## Rules`` was deleted outright from ``conflict-auditor.md``, five
      substantive rules with no surviving copy. That file carries four fences and has never
      had ``rules``.

    The guard written after the first loss refuses when the removal span *encloses* a live
    fence. That is a different property and it did not fire for the second: the span enclosed
    nothing. This is the check the guard's own comment already described — *merge the fence in
    first; only then is the unfenced copy a duplicate* — stated as code rather than prose.

    Returns None when no deployed fence carries the heading, or when more than one does (which
    survivor? refuse rather than choose).
    """
    regions = _extract_fenced_regions(text)
    if not isinstance(regions, dict) or not regions:
        return None
    twins = [b for b in regions.values() if _norm(heading) in _norm(_fence_body(b))]
    return twins[0] if len(twins) == 1 else None


_FENCE_BEGIN_LINE_RE = re.compile(r"^<!--\s*AGENTTEAMS:BEGIN\s+\S+.*?-->\s*$", re.MULTILINE)


def _span_bounded_at_first_fence(text: str, heading: str, start: int) -> tuple[int, int] | None:
    """Span of an unfenced section that ends where managed content begins.

    :func:`_unfenced_section_span` ends a section at the next heading of the same-or-higher
    level. When the fenced twin sits BELOW the unfenced one, that next heading is the twin's own
    heading *inside the fence*, so the span swallows every fence in between and the enclosure
    guard refuses. That refused 8 collisions in this repository's team — every one of them in a
    file that already carried the fence — while advising a merge that would have changed
    nothing and which has twice destroyed content.

    A section ends where the managed content begins. Bounding at the first fence BEGIN after the
    heading makes the removed span fence-free by construction.

    **The bound is not the authorisation.** Two things could still go wrong, and each has its
    own guard:

    * A section that legitimately *continues past* a fence would be truncated here. The caller's
      equality test against the deployed fence body refuses it: a partial section cannot equal
      the whole.
    * A distant first fence would let the span swallow whole sibling sections. Refused here — a
      section cannot contain a heading of its own level or higher.

    Args:
        text:    The deployed file's content.
        heading: The duplicated heading line.
        start:   Character offset of the unfenced occurrence.

    Returns:
        ``(start, end)`` bounded at the first following fence, or None when no fence follows or
        a sibling heading intervenes.
    """
    level = len(heading) - len(heading.lstrip("#"))
    m = _FENCE_BEGIN_LINE_RE.search(text, start + len(heading))
    if m is None:
        return None
    end = m.start()
    for h in re.finditer(r"^(#{1,6})\s+\S", text[start + len(heading):end], re.MULTILINE):
        if len(h.group(1)) <= level:
            return None
    return start, end


def _fence_whose_first_heading_is(text: str, heading: str) -> str | None:
    """The fence this heading *belongs to*, if exactly one leads with it.

    Deliberately narrower than :func:`_deployed_fence_carrying`, and deliberately separate.

    The safety predicate asks "will a copy survive this removal?", so containment is the right
    test there: a heading nested inside a fenced block genuinely does survive. This asks a
    different question — "are these two texts two versions of the same section?" — and
    containment is the wrong test for it. ``invariant_core`` carries several sub-headings, so an
    unfenced ``### Core Responsibilities`` was being compared against the whole ``invariant_core``
    body: never equal, never resolvable, reported forever. Three of this repository's ten
    collisions were that, and a review list that is 30% permanent noise stops being read.

    **Not merged with the safety predicate on purpose.** Narrowing that one to remove this noise
    would let a real survivor stop counting whenever it does not lead its fence, re-opening the
    hole that deleted ``## Rules`` from ``conflict-auditor.md`` on 2026-08-03.

    Returns None when no fence leads with the heading, or when more than one does.
    """
    regions = _extract_fenced_regions(text)
    if not isinstance(regions, dict) or not regions:
        return None
    leads = []
    for body in regions.values():
        for line in _fence_body(body).strip().splitlines():
            if line.strip():
                if line.strip() == heading:
                    leads.append(body)
                break
    return leads[0] if len(leads) == 1 else None


def _trailing_duplicates_a_deployed_fence(text: str, heading: str) -> bool:
    """Is the trailing unfenced section an exact copy of a fence already in this file?

    The authorisation for bounding a trailing section at end-of-file. If the text from the
    unfenced heading to EOF equals the body of a fenced region *in the same deployed file*,
    then removing it deletes nothing that does not survive inside that fence. The proof is
    local: no provenance, no build log, no git ref, and no ``--trust-provenance``. That flag's
    last use gutted a deployed ``security.md`` from 363 lines to 32, and it answers a different
    question — "has anyone edited this file?" — which is not the one that makes a delete safe.

    Compared against the **deployed** fence, never the incoming render. The recurring defect in
    this script is exactly that substitution: a template fence proves nothing about what is on
    disk, so a file whose live fence has drifted from its template must still refuse.

    "Trailing" guarantees no heading of the same-or-higher level follows — *not* that no text
    follows. An operator note appended under the duplicate carries no heading, so the heading
    walk still reports ``trailing`` while bounding at EOF would destroy the note. The equality
    test is what catches that: the tail no longer matches the fence.

    Refuses on anything ambiguous — unparseable fences, no fenced twin, more than one fenced
    twin, more than one unfenced occurrence.

    Args:
        text:    The deployed file's current content.
        heading: The duplicated heading, e.g. ``"## Operational integration"``.

    Returns:
        True only when the equality holds and the twin is unique in both directions.
    """
    twin = _deployed_fence_carrying(text, heading)
    if twin is None:
        return False

    starts = _unfenced_starts(text, heading)
    if len(starts) != 1:
        return False

    return _norm(text[starts[0]:]) == _norm(_fence_body(twin))


def _unfenced_section_span(text: str, heading: str) -> tuple[int, int] | str:
    """Character span of the single unfenced occurrence of *heading* and its body.

    Returns ``(start, end)`` on success, or a reason string when the section cannot be bounded:
    ``"absent"``, ``"duplicated"`` (more than one unfenced occurrence, so the boundaries are
    ambiguous), or ``"trailing"`` (no following heading of the same-or-higher level, so the
    section runs to end-of-file and bounding it there would take any trailing region with it).

    The three were one refusal until the closeout needed to know which: they have different
    remedies and conflating them made the report say "cannot bound" 6 times without saying why.
    """
    unfenced = set(unfenced_lines(text))
    level = len(heading) - len(heading.lstrip("#"))

    # Per-OCCURRENCE, not per-file. The original condition asked whether the heading
    # appears among unfenced lines at all and then kept every regex match, fenced ones
    # included — so a heading with a fenced twin always counted twice and returned
    # "duplicated". That refused all 20 real collisions in this repo's own team as
    # unboundable, which is the state that made them look unresolvable.
    starts = _unfenced_starts(text, heading)
    if len(starts) != 1:
        return "duplicated" if starts else "absent"
    start = starts[0]

    rest = text[start + len(heading):]
    following = None
    for m in re.finditer(r"^(#{1,6})\s+\S", rest, re.MULTILINE):
        if len(m.group(1)) <= level:
            following = m.start()
            break
    if following is None:
        return "trailing"                 # runs to EOF — refuse to bound there
    return start, start + len(heading) + following


def _resolve_file(
    deployed: Path,
    fresh: str,
    *,
    agents_dir: Path | None = None,
    trust_provenance: bool = False,
    args_ref: str = "HEAD",
) -> tuple[str | None, list[str], list[str]]:
    """Return (new_text_or_None, resolved_headings, skipped_reports) for one file.

    ``trust_provenance`` enables the second authorisation described in
    :func:`_file_is_pristine`. Off by default and deliberately so: it removes content that does
    *not* match the template, which the equality proof would refuse.
    """
    text = deployed.read_text(encoding="utf-8")
    result = _merge_fenced_content(fresh, text)

    # Two detection paths, because one alone is blind half the time.
    #
    # The merge notices fire only while a fence is being ADDED. After --update --merge has
    # added it, the pre-existing unfenced twin remains but produces no notice — this repo's
    # own team carried 23 such duplicates in 17 files that the resolver reported as 0.
    # `_duplicate_headings_in_file` reads the deployed file directly and finds a heading
    # that sits both inside a fence and outside one, which is exactly that residue.
    headings: list[str] = []
    for notice in result.duplicate_section_notices:
        m = re.search(r"duplicate section '([^']+)'", notice)
        if m and m.group(1) not in headings:
            headings.append(m.group(1))
    for heading in _duplicate_headings_in_file(text):
        if heading not in headings:
            headings.append(heading)

    # A heading that only ever appears NESTED inside a fenced block is not a duplicated
    # section — it is a sub-heading of one. Comparing its unfenced occurrence against the whole
    # enclosing fence can never match, so it was reported as unresolvable on every run forever:
    # three of this repository's ten collisions were that. Dropped from the collision list
    # rather than refused, because a permanent entry in a human-review report is noise, and a
    # report that is 30% noise stops being read.
    #
    # This filters what is COMPARABLE. It does not touch `_deployed_fence_carrying`, which
    # answers the different and load-bearing question of whether a copy survives a removal.
    headings = [
        h for h in headings
        if _deployed_fence_carrying(text, h) is None      # no survivor: still a real refusal
        or _fence_whose_first_heading_is(text, h) is not None
    ]
    if not headings:
        return None, [], []

    regions = _extract_fenced_regions(fresh)
    if not isinstance(regions, dict):
        return None, [], [f"{deployed.name}: fresh render does not parse ({regions})"]

    resolved: list[str] = []
    skipped: list[str] = []
    working = text
    for heading in headings:

        incoming = next(
            (b for b in regions.values() if _norm(heading) in _norm(_fence_body(b))), None
        )
        if incoming is None:
            skipped.append(f"{deployed.name}: {heading!r} — no incoming fenced body carries it")
            continue

        span = _unfenced_section_span(working, heading)
        # A trailing section that is an exact copy of a fence already in this file can be
        # bounded at EOF without provenance: the content survives inside the fence. Tried
        # before the provenance route because it proves more with less — see
        # `_trailing_duplicates_a_deployed_fence`.
        if span == "trailing" and _trailing_duplicates_a_deployed_fence(working, heading):
            span = _trailing_span(working, heading)
        if span == "trailing" and trust_provenance and agents_dir is not None:
            # A trailing section can only swallow a user region if one exists below it. Reference
            # files never receive `## Project-Specific Notes` (emit._is_agent_doc excludes them),
            # and a pristine file contains no operator content anywhere. Requiring BOTH is what
            # makes bounding at end-of-file safe here; either alone would not be.
            if _PROJECT_NOTES not in working and _file_is_pristine(agents_dir, deployed, args_ref):
                span = _trailing_span(working, heading)
        if isinstance(span, str):
            skipped.append(f"{deployed.name}: {heading!r} — cannot bound: {span}")
            continue

        start, end = span
        # A span containing a live fence is managed content by definition, and removing it
        # is deletion rather than deduplication. `incoming` comes from the FRESH render, so
        # a template fence proves nothing about what the deployed file carries: the live
        # loss removed `## Invariant Core` from security.md on the strength of the
        # template's `invariant_core` fence, which that file has never received. The span
        # ran 363 lines -> 32 and took the file's own two fenced regions with it, leaving
        # zero. Merge the fence in first; only then is the unfenced copy a duplicate.
        # The survivor must already exist HERE, not merely in the render. Checked before the
        # enclosure guard because it is the stronger condition: enclosure asks whether the span
        # would take managed content with it, this asks whether anything is left afterwards.
        survivor = _deployed_fence_carrying(working, heading)
        if survivor is None:
            skipped.append(
                f"{deployed.name}: {heading!r} — this file has no fence carrying that section, "
                f"so removing the unfenced copy would delete the only one. "
                f"Run --update --merge first, then re-run."
            )
            continue

        enclosed = _FENCE_BEGIN_COUNT_RE.findall(working[start:end])
        if enclosed:
            # The twin sits BELOW the unfenced copy, so the "next same-or-higher heading" that
            # bounded the span is the twin's own heading inside its fence, and the span
            # overshot across every fence in between. Re-bound where the managed content
            # begins, and require equality against the DEPLOYED survivor — not the render,
            # which proves nothing about this file.
            rebound = _span_bounded_at_first_fence(working, heading, start)
            if rebound is not None and _norm(
                working[rebound[0]:rebound[1]]
            ) == _norm(_fence_body(survivor)):
                start, end = rebound
            else:
                # `survivor is not None` above, so the fence is already here: recommending a
                # merge would send the operator to the one operation that has twice destroyed
                # content, to change nothing. Say what is actually true instead.
                reason = (
                    "the unfenced copy is not identical to it"
                    if rebound is not None
                    else "the section continues past the fence or contains a sibling heading"
                )
                skipped.append(
                    f"{deployed.name}: {heading!r} — this file already carries the fenced copy "
                    f"({', '.join(enclosed[:3])}), but {reason}, so the two cannot be "
                    f"reconciled automatically. Review by hand."
                )
                continue
        if _norm(working[start:end]) != _norm(_fence_body(incoming)):
            pristine = (
                trust_provenance
                and agents_dir is not None
                and _file_is_pristine(agents_dir, deployed, args_ref)
            )
            if not pristine:
                skipped.append(
                    f"{deployed.name}: {heading!r} — deployed copy differs from the template's; "
                    f"review by hand"
                )
                continue
            working = working[:start] + working[end:]
            resolved.append(f"{heading}  [provenance]")
            continue

        working = working[:start] + working[end:]
        resolved.append(f"{heading}  [equality]")

    return (working if resolved else None), resolved, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--brief", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path, help="deployed agents directory")
    ap.add_argument("--framework", default="claude",
                    choices=["claude", "copilot-vscode", "copilot-cli"],
                    help="framework the deployed team was generated with (default: claude)")
    ap.add_argument("--trust-provenance", action="store_true",
                    help="also resolve a differing copy when the file is byte-identical to what "
                         "agentteams last emitted (build-log hash) — a wider authorisation than "
                         "the equality proof; see _file_is_pristine")
    ap.add_argument("--provenance-ref", default="HEAD",
                    help="git ref holding the pre-change state, used when a prior run of this "
                         "script has already modified the working tree (default: HEAD)")
    ap.add_argument("--apply", action="store_true", help="write changes (default: report only)")
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory() as td:
        generated = Path(td)
        _run_pipeline(args.brief, generated, args.framework)

        planned: dict[Path, str] = {}
        all_resolved: list[str] = []
        all_skipped: list[str] = []
        unmatched: list[str] = []
        for deployed in sorted(args.output.rglob("*.md")):
            if ".agentteams-backups" in deployed.parts:
                continue
            rel = deployed.relative_to(args.output)
            match = next(
                (c for c in generated.rglob(rel.name)
                 if c.relative_to(generated).as_posix().endswith(rel.as_posix())),
                None,
            )
            if match is None:
                unmatched.append(str(rel))
                continue
            new_text, resolved, skipped = _resolve_file(
                deployed, match.read_text(encoding="utf-8"),
                agents_dir=args.output, trust_provenance=args.trust_provenance,
                args_ref=args.provenance_ref,
            )
            all_skipped.extend(skipped)
            if new_text is not None:
                planned[deployed] = new_text
                all_resolved.extend(f"{rel}: {h}" for h in resolved)

    if unmatched:
        # Loud, because silence here is what made the first run look successful while it was
        # comparing against the wrong framework's output entirely.
        print(f"WARNING: {len(unmatched)} deployed file(s) had no matching render and were NOT "
              f"examined — is --framework right?")
        for u in unmatched[:10]:
            print(f"      ? {u}")
        print()
    print(f"Resolvable: {len(all_resolved)} collision(s) in {len(planned)} file(s)")
    for line in all_resolved:
        print(f"  - {line}")
    if all_skipped:
        print(f"\nNeeds review: {len(all_skipped)}")
        for line in all_skipped:
            print(f"  ! {line}")

    if not args.apply:
        print("\n(report only — pass --apply to write, which takes a backup first)")
        return 0
    if not planned:
        print("\nNothing to write.")
        return 0

    backup = backup_output_dir(
        args.output,
        files_to_backup=[str(p.relative_to(args.output)) for p in planned],
        reason="resolve-fence-collisions",
    )
    print(f"\nBacked up {len(planned)} file(s) to {getattr(backup, 'backup_path', '?')}")
    for path, new_text in planned.items():
        path.write_text(new_text, encoding="utf-8")
    print(f"Wrote {len(planned)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
