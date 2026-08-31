#!/usr/bin/env python3
"""check-skeleton.py — conformance gate for the agentteams-security handbook.

Each edition must carry EXACTLY the skeleton's sections for its dial — no missing, no extra, no
sections it was dialed to Skip. This checks the STRUCTURAL half only (facts are left to the
verification pass described in projection-guide.md). Exit 0 = conformant (or not-yet-scaffolded);
exit 1 = drift.

Run from anywhere:  python3 _meta/check-skeleton.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKELETON = ROOT / "SKELETON.md"

# edition dir -> dial letter
EDITIONS = {
    "reference": "R",
    "for-developers": "D",
    "for-researchers": "S",
    "for-everyone": "E",
}

SECTION_RE = re.compile(r"^#{3,4}\s+(S\d+(?:\.\d+)?)\s+—\s+(.+?)\s*$")
SUBSEC_RE = re.compile(r"^\*\*(S\d+\.\d+)\s+(.+?)\.?\*\*")
DIAL_RE = re.compile(r"\*\*Dial\.\*\*\s*(.+)$")
DIAL_TOKEN = re.compile(r"\b([RDSE])\s+(Full|Core|Light|Skip)\b")

MARKER_RES = (
    re.compile(r"\{#(S\d+(?:\.\d+)?)\}"),
    re.compile(r"skeleton:(S\d+(?:\.\d+)?)"),
    re.compile(r"\[(S\d+(?:\.\d+)?)\]"),
)


def parse_skeleton() -> dict[str, dict[str, str]]:
    """Return {section_id: {letter: dial}} for every section that carries a Dial line."""
    dials: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in SKELETON.read_text(encoding="utf-8").splitlines():
        m = SECTION_RE.match(line) or SUBSEC_RE.match(line)
        if m:
            current = m.group(1)
            dials.setdefault(current, {})
            continue
        d = DIAL_RE.search(line)
        if d and current:
            for letter, dial in DIAL_TOKEN.findall(d.group(1)):
                dials[current][letter] = dial
    # keep only sections that actually carry a dial line (drops bare Part/container headers)
    return {sid: d for sid, d in dials.items() if d}


def id_markers(edition_dir: Path) -> set[str]:
    found: set[str] = set()
    for md in edition_dir.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        for rx in MARKER_RES:
            found.update(rx.findall(text))
    return found


def main() -> int:
    dials = parse_skeleton()
    all_ids = set(dials)
    drift = False
    for edition, letter in EDITIONS.items():
        edir = ROOT / edition
        mds = list(edir.rglob("*.md")) if edir.exists() else []
        if not mds:
            print(f"[skip] {edition}: not yet scaffolded")
            continue
        present = id_markers(edir)
        required = {sid for sid in all_ids if dials[sid].get(letter, "Full") != "Skip"}
        skipped = {sid for sid in all_ids if dials[sid].get(letter) == "Skip"}
        missing = required - present
        extra = present - all_ids
        wrongly_present = present & skipped
        if missing or extra or wrongly_present:
            drift = True
            print(f"[DRIFT] {edition}:")
            if missing:
                print(f"    missing: {sorted(missing)}")
            if extra:
                print(f"    unknown IDs (not in skeleton): {sorted(extra)}")
            if wrongly_present:
                print(f"    present but dialed Skip: {sorted(wrongly_present)}")
        else:
            print(f"[ok] {edition}: {len(present)} sections conform")
    if drift:
        print("\nConformance FAILED — fix the skeleton first, then re-project (see projection-guide.md).")
        return 1
    print("\nAll scaffolded editions conform.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
