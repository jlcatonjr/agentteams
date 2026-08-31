#!/usr/bin/env python3
"""scaffold.py — project SKELETON.md into an edition's heading tree.

The mechanical "import": reads SKELETON.md and, for a given edition, writes/refreshes
  (a) <edition>/README.md      — a TOC table (section -> ID + this edition's dial)
  (b) <edition>/<part-slug>.md — one file per Part, each rendered section a heading marked {#Sx}
      with the canonical facts inlined as an author comment and a "to be authored" placeholder.

Non-destructive: a section stub is written only if its {#Sx} anchor is not already present in the
edition. `--force` rewrites anyway. Skip-dialed sections are excluded from part files (but still get a
_Skip_ row in the README TOC).

Usage:
    python3 _meta/scaffold.py reference
    python3 _meta/scaffold.py for-everyone --force
    python3 _meta/scaffold.py --all
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKELETON = ROOT / "SKELETON.md"

EDITIONS = {
    "reference": "R",
    "for-developers": "D",
    "for-researchers": "S",
    "for-everyone": "E",
}

PART_RE = re.compile(r"^##\s+(Part\s+[IVX]+\b.*)$")
SECTION_RE = re.compile(r"^#{3,4}\s+(S\d+(?:\.\d+)?)\s+—\s+(.+?)\s*$")
SUBSEC_RE = re.compile(r"^\*\*(S\d+\.\d+)\s+(.+?)\.?\*\*")
DIAL_RE = re.compile(r"\*\*Dial\.\*\*\s*(.+)$")
DIAL_TOKEN = re.compile(r"\b([RDSE])\s+(Full|Core|Light|Skip)\b")
FACT_RE = re.compile(r"^\d+\.\s+(.+)$")


def slug(part_title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", part_title.lower()).strip("-")


def parse() -> list[dict]:
    out: list[dict] = []
    part = ""
    cur: dict | None = None
    collecting_facts = False
    for line in SKELETON.read_text(encoding="utf-8").splitlines():
        p = PART_RE.match(line)
        if p:
            part = p.group(1)
            continue
        m = SECTION_RE.match(line) or SUBSEC_RE.match(line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2).strip(),
                   "part": part, "dials": {}, "facts": []}
            out.append(cur)
            collecting_facts = False
            continue
        if cur is None:
            continue
        if line.strip().startswith("**Canonical facts."):
            collecting_facts = True
            continue
        if line.strip().startswith("**Source.") or line.strip().startswith("**Dial."):
            collecting_facts = False
        d = DIAL_RE.search(line)
        if d:
            for letter, dial in DIAL_TOKEN.findall(d.group(1)):
                cur["dials"][letter] = dial
        if collecting_facts:
            f = FACT_RE.match(line.strip())
            if f and len(cur["facts"]) < 12:
                cur["facts"].append(f.group(1).strip())
    # only sections that carry a dial line (drops bare Part/container headers)
    return [s for s in out if s["dials"]]


def scaffold(edition: str, force: bool) -> None:
    letter = EDITIONS[edition]
    edir = ROOT / edition
    edir.mkdir(parents=True, exist_ok=True)
    sections = parse()

    # collect existing anchors across the edition
    existing = ""
    for md in edir.rglob("*.md"):
        existing += md.read_text(encoding="utf-8")

    toc_rows: list[str] = []
    # group by part, preserving order
    parts: list[str] = []
    for s in sections:
        if s["part"] not in parts:
            parts.append(s["part"])

    for part in parts:
        part_sections = [s for s in sections if s["part"] == part]
        fname = edir / f"{slug(part)}.md"
        header = f"# {part}\n\n" if not fname.exists() else ""
        chunks: list[str] = []
        for s in part_sections:
            dial = s["dials"].get(letter, "Full")
            anchor = f"{{#{s['id']}}}"
            if dial == "Skip":
                toc_rows.append(f"| {s['id']} | {s['title']} | _Skip_ |")
                continue
            toc_rows.append(f"| [{s['id']}]({slug(part)}.md#{s['id']}) | {s['title']} | {dial} |")
            if anchor in existing and not force:
                continue
            facts = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(s["facts"]))
            chunks.append(
                f"## {s['title']} {anchor}\n"
                f"<!-- dial: {letter}={dial} — render per audience-profiles.md; "
                f"do not add facts not in SKELETON {s['id']}; preserve honest ceilings -->\n"
                f"<!-- canonical facts to project:\n{facts}\n-->\n\n"
                f"_(to be authored — Edition {letter}, depth {dial})_\n"
            )
        if chunks:
            with fname.open("a", encoding="utf-8") as fh:
                if header:
                    fh.write(header)
                fh.write("\n".join(chunks) + "\n")

    readme = edir / "README.md"
    if force or not readme.exists():
        toc = "\n".join(toc_rows)
        readme.write_text(
            f"# Edition {letter} — `{edition}/`\n\n"
            f"Projected from [`../SKELETON.md`](../SKELETON.md) per "
            f"[`../audience-profiles.md`](../audience-profiles.md).\n\n"
            f"| ID | Section | Dial |\n|---|---|---|\n{toc}\n",
            encoding="utf-8",
        )
    print(f"[scaffold] {edition}: {len(sections)} sections processed")


def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    targets = list(EDITIONS) if (args == ["--all"] or not args) else args
    for t in targets:
        if t not in EDITIONS:
            print(f"unknown edition: {t} (choices: {', '.join(EDITIONS)} or --all)")
            return 2
        scaffold(t, force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
