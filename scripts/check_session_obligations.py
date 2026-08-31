#!/usr/bin/env python3
"""check_session_obligations.py — report constitutional obligations with no evidence.

The project's constitution requires certain artifacts: a plan carries a steps
CSV; a plan reaching all-``done`` is captured in a daily work summary; a session
that produced a deliverable logs any tool-facing remediation. This session
produced many deliverables, logged none, and **nothing noticed** — the
remediation log had not been touched for five days.

That is the failure this tool addresses, and it addresses it in the only way the
principle allows.

**It reports absent evidence, not violation.** It can see that no remediation
entry exists for today. It cannot see whether a retrospective was performed and
judged to yield nothing, which is a legitimate outcome. The distinction is not
pedantry: an instrument that called the second a violation would be enforcing a
rule about judgment, and would be routed around rather than followed.

**It is not a gate.** A constitution enables accountability for what it cannot
prevent. Blocking a session on an unwritten retrospective would try to prevent
it, and would convert a governance obligation into an obstacle. This exits 0
whatever it finds.

Usage::

    python3 scripts/check_session_obligations.py
    python3 scripts/check_session_obligations.py --week 2026-W31
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BY_WEEK = ROOT / "tmp" / "by-week"
SUMMARIES = ROOT / "workSummaries" / "daily"
REMEDIATION_LOG = ROOT / "references" / "agentteams-remediation-log.csv"
REPORT = ROOT / "tmp" / "session-obligations.report.md"


def _iso_week(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _plan_complete(steps_csv: Path) -> bool:
    """True when every row in a steps CSV is marked done."""
    try:
        rows = list(csv.DictReader(steps_csv.open(encoding="utf-8")))
    except (OSError, csv.Error):
        return False
    statuses = [(r.get("status") or "").strip().lower() for r in rows]
    return bool(statuses) and all(s == "done" for s in statuses)


def check(week: str, today: date) -> list[dict]:
    """Collect obligations whose evidence is absent.

    Args:
        week: ISO week folder name, e.g. ``2026-W31``.
        today: Date used for the daily-summary and remediation-log checks.

    Returns:
        Findings as dicts with keys obligation/subject/observation.
    """
    findings: list[dict] = []
    week_dir = BY_WEEK / week

    def add(obligation: str, subject: str, observation: str) -> None:
        findings.append(
            {"obligation": obligation, "subject": subject, "observation": observation}
        )

    plans = sorted(week_dir.glob("*.plan.md")) if week_dir.is_dir() else []
    completed_plans: list[Path] = []

    for plan in plans:
        steps = plan.with_name(plan.name.replace(".plan.md", ".steps.csv"))
        if not steps.exists():
            add(
                "Rule 9 — every plan carries a steps CSV",
                plan.name,
                f"no {steps.name} beside it",
            )
        elif _plan_complete(steps):
            completed_plans.append(plan)

    if completed_plans:
        summary = SUMMARIES / f"{today.isoformat()}.md"
        if not summary.exists():
            add(
                "Rule 12 — completed plans captured in a daily work summary",
                ", ".join(p.stem for p in completed_plans),
                f"no {summary.relative_to(ROOT)}",
            )

    if REMEDIATION_LOG.exists():
        try:
            rows = list(csv.DictReader(REMEDIATION_LOG.open(encoding="utf-8")))
        except (OSError, csv.Error):
            rows = []
        stamps = {(r.get("date") or "").strip() for r in rows}
        if plans and today.isoformat() not in stamps:
            add(
                "Rule 11 — retrospective logs tool-facing remediation",
                f"{len(plans)} plan(s) this week",
                f"no entry dated {today.isoformat()} in "
                f"{REMEDIATION_LOG.relative_to(ROOT)}",
            )
    else:
        add(
            "Rule 11 — retrospective logs tool-facing remediation",
            "remediation log",
            "log file not found",
        )

    return findings


def write_report(week: str, findings: list[dict], today: date) -> None:
    """Write the obligations report under tmp/, per the report convention."""
    lines = [
        "# session obligations — report",
        "",
        f"Generated {today.isoformat()} for week {week}.",
        "",
        "**Absent evidence, not violation.** This lists obligations for which no",
        "artifact was found. An obligation may have been considered and judged",
        "to require nothing; that is invisible here and is a legitimate outcome.",
        "This report is not a gate and never changes an exit code.",
        "",
    ]
    if not findings:
        lines += ["## All obligations", "", "- PASS", ""]
    else:
        for f in findings:
            lines.append(f"## {f['obligation']}")
            lines.append(f"- REVIEW: {f['subject']} — {f['observation']}")
            lines.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Entry point. Always returns 0 — this reports, it does not gate."""
    argv = sys.argv[1:] if argv is None else argv
    today = date.today()
    week = _iso_week(today)
    if "--week" in argv:
        week = argv[argv.index("--week") + 1]

    findings = check(week, today)
    write_report(week, findings, today)

    print(f"week={week}  obligations without evidence: {len(findings)}")
    print(f"wrote {REPORT.relative_to(ROOT)}\n")
    for f in findings:
        print(f"  REVIEW  {f['obligation']}")
        print(f"          {f['subject']} — {f['observation']}")
    if not findings:
        print("  every obligation has a corresponding artifact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
