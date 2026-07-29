#!/usr/bin/env python3
"""verify_audit_ledger.py — structural verification of template-chapter-audit.csv.

The ledger records how each shipped template relates to the book's architecture.
Nothing regenerated it, so it drifted: rows understated a rule count by five and
ranked four already-implemented functions as high-severity gaps.

This tool is **report-only**. It never edits the ledger. Detection and
remediation stay separate, which is the same separation the module's own
code-hygiene agent observes.

What it can check (structural claims):

  * ``template_file`` — named template exists, or is correctly recorded as absent
  * ``implementing_surface`` — named module/path resolves on disk
  * ``disposition`` — drawn from the closed vocabulary
  * coherence — a row cannot claim ``absent`` while naming an implementing surface
  * staleness — ``verified_on`` predates the last modification of a named surface

What it cannot check (semantic claims): whether a row's prose *describes* the
template correctly, whether a severity is proportionate, or whether a gap
matters. Those remain judgment. A clean report means the ledger's structural
claims hold, not that the ledger is right.

Usage::

    python3 scripts/verify_audit_ledger.py            # report to stdout + file
    python3 scripts/verify_audit_ledger.py --quiet    # file only

Exit code is 0 unless a row makes a structurally false claim.
"""
from __future__ import annotations

import csv
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "agentteams" / "templates" / "template-chapter-audit.csv"
TEMPLATES = ROOT / "agentteams" / "templates"
REPORT = LEDGER.parent / "template-chapter-audit.report.md"

DISPOSITIONS = {
    "absent",
    "discharged-operationally",
    "superseded",
    "by-design",
    "unreviewed",
}

OK, REVIEW, DEFECT = "OK", "REVIEW", "DEFECT"


def _last_commit_date(path: Path) -> str | None:
    """Return the YYYY-MM-DD of the last commit touching *path*, if available."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ad", "--date=short", "--", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    stamp = out.stdout.strip()
    return stamp or None


def _resolve(surface: str) -> Path | None:
    """Resolve an implementing_surface value to a path on disk.

    Accepts ``pkg/mod.py`` and ``pkg/mod.py::symbol`` forms.
    """
    if not surface:
        return None
    bare = surface.split("::", 1)[0].strip()
    candidate = ROOT / bare
    return candidate if candidate.exists() else None


def verify() -> tuple[list[dict], dict]:
    """Check every ledger row's structural claims.

    Returns:
        (findings, stats) where findings is a list of dicts with keys
        audit_id/status/issue, and stats summarises the run.
    """
    rows = list(csv.DictReader(LEDGER.open(encoding="utf-8")))
    findings: list[dict] = []

    def add(aid: str, status: str, issue: str) -> None:
        findings.append({"audit_id": aid, "status": status, "issue": issue})

    for r in rows:
        aid = r.get("audit_id", "?")
        disp = (r.get("disposition") or "").strip()
        surface = (r.get("implementing_surface") or "").strip()
        verified = (r.get("verified_on") or "").strip()
        tfile = (r.get("template_file") or "").strip()

        if disp and disp not in DISPOSITIONS:
            add(aid, DEFECT, f"disposition '{disp}' is outside the vocabulary")
        if not disp:
            add(aid, REVIEW, "no disposition recorded")
        elif disp == "unreviewed":
            # Silence here would be the instrument's worst failure: a clean
            # report over rows nobody has checked reads as verification.
            add(aid, REVIEW, "disposition is 'unreviewed' — claim not yet checked")

        # A row cannot both claim nothing implements the function and name what does.
        if disp == "absent" and surface:
            add(aid, DEFECT, f"disposition 'absent' but names surface '{surface}'")
        if disp == "discharged-operationally" and not surface:
            add(aid, DEFECT, "claims operational discharge but names no surface")

        if surface and _resolve(surface) is None:
            add(aid, DEFECT, f"implementing_surface '{surface}' does not resolve")

        # template_file is relative to the templates dir; "—" means deliberately none.
        if tfile and tfile not in {"—", "-", ""} and not (TEMPLATES / tfile).exists():
            add(aid, DEFECT, f"template_file '{tfile}' does not exist")

        if disp and disp != "unreviewed" and not verified:
            add(aid, REVIEW, f"disposition '{disp}' with no verified_on date")

        if verified and surface:
            resolved = _resolve(surface)
            if resolved is not None:
                changed = _last_commit_date(resolved)
                if changed and changed > verified:
                    add(
                        aid,
                        REVIEW,
                        f"surface changed {changed}, last verified {verified}",
                    )

    stats = {
        "rows": len(rows),
        "unreviewed": sum(
            1 for r in rows if (r.get("disposition") or "").strip() == "unreviewed"
        ),
        "defects": sum(1 for f in findings if f["status"] == DEFECT),
        "reviews": sum(1 for f in findings if f["status"] == REVIEW),
    }
    return findings, stats


def write_report(findings: list[dict], stats: dict) -> None:
    """Write the report beside the ledger, per the repo's report convention."""
    lines = [
        "# template-chapter-audit — verification report",
        "",
        f"Generated {date.today().isoformat()} by `scripts/verify_audit_ledger.py`.",
        "Structural claims only; prose accuracy and severity remain judgment.",
        "",
        f"- rows: {stats['rows']}",
        f"- unreviewed dispositions: {stats['unreviewed']}",
        f"- defects: {stats['defects']}",
        f"- reviews: {stats['reviews']}",
        "",
    ]
    if not findings:
        lines += ["## All rows", "", "- PASS", ""]
    else:
        by_id: dict[str, list[dict]] = {}
        for f in findings:
            by_id.setdefault(f["audit_id"], []).append(f)
        for aid in sorted(by_id):
            lines.append(f"## {aid}")
            for f in by_id[aid]:
                verb = "FAIL" if f["status"] == DEFECT else "REVIEW"
                lines.append(f"- {verb}: {f['issue']}")
            lines.append("")
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns 1 if any row makes a structurally false claim."""
    argv = sys.argv[1:] if argv is None else argv
    findings, stats = verify()
    write_report(findings, stats)

    if "--quiet" not in argv:
        print(
            f"rows={stats['rows']}  unreviewed={stats['unreviewed']}  "
            f"defects={stats['defects']}  reviews={stats['reviews']}"
        )
        print(f"wrote {REPORT.relative_to(ROOT)}\n")
        for f in findings:
            if f["status"] == DEFECT:
                print(f"  DEFECT  {f['audit_id']:8} {f['issue']}")
        shown = sum(1 for f in findings if f["status"] == DEFECT)
        if stats["reviews"]:
            print(f"  ({stats['reviews']} review item(s) in the report)")
        if not shown:
            print("  no structurally false claims")
    return 1 if stats["defects"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
