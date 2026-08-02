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
   destroyed by the tool cleaning up after it. Resolution requires a following heading of the
   same or higher level; a trailing section is reported, never touched.
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


def _unfenced_section_span(text: str, heading: str) -> tuple[int, int] | None:
    """Character span of the single unfenced occurrence of *heading* and its body.

    Returns ``None`` when the section cannot be bounded safely: the heading is absent, appears
    more than once outside a fence, or has no following heading of the same-or-higher level
    (in which case it runs to end-of-file and would take any trailing user region with it).
    """
    unfenced = set(unfenced_lines(text))
    level = len(heading) - len(heading.lstrip("#"))

    starts = [
        m.start()
        for m in re.finditer(rf"^{re.escape(heading)}\s*$", text, re.MULTILINE)
        if any(line.strip() == heading.strip() for line in unfenced)
    ]
    if len(starts) != 1:
        return None
    start = starts[0]

    rest = text[start + len(heading):]
    following = None
    for m in re.finditer(r"^(#{1,6})\s+\S", rest, re.MULTILINE):
        if len(m.group(1)) <= level:
            following = m.start()
            break
    if following is None:
        return None                       # trailing section — refuse to bound at EOF
    return start, start + len(heading) + following


def _resolve_file(deployed: Path, fresh: str) -> tuple[str | None, list[str], list[str]]:
    """Return (new_text_or_None, resolved_headings, skipped_reports) for one file."""
    text = deployed.read_text(encoding="utf-8")
    result = _merge_fenced_content(fresh, text)
    if not result.duplicate_section_notices:
        return None, [], []

    regions = _extract_fenced_regions(fresh)
    if not isinstance(regions, dict):
        return None, [], [f"{deployed.name}: fresh render does not parse ({regions})"]

    resolved: list[str] = []
    skipped: list[str] = []
    working = text
    for notice in result.duplicate_section_notices:
        m = re.search(r"duplicate section '([^']+)'", notice)
        if not m:
            continue
        heading = m.group(1)

        incoming = next(
            (b for b in regions.values() if _norm(heading) in _norm(_fence_body(b))), None
        )
        if incoming is None:
            skipped.append(f"{deployed.name}: {heading!r} — no incoming fenced body carries it")
            continue

        span = _unfenced_section_span(working, heading)
        if span is None:
            skipped.append(
                f"{deployed.name}: {heading!r} — cannot bound the unfenced copy safely "
                f"(absent, duplicated, or trailing at end-of-file)"
            )
            continue

        start, end = span
        if _norm(working[start:end]) != _norm(_fence_body(incoming)):
            skipped.append(
                f"{deployed.name}: {heading!r} — deployed copy differs from the template's; "
                f"review by hand"
            )
            continue

        working = working[:start] + working[end:]
        resolved.append(heading)

    return (working if resolved else None), resolved, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--brief", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path, help="deployed agents directory")
    ap.add_argument("--framework", default="claude",
                    choices=["claude", "copilot-vscode", "copilot-cli"],
                    help="framework the deployed team was generated with (default: claude)")
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
                deployed, match.read_text(encoding="utf-8")
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
