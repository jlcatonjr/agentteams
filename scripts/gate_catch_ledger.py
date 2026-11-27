#!/usr/bin/env python3
"""gate_catch_ledger.py — derive a per-gate catch ledger from the governance logs a team already keeps.

**Why.** A process gate (an audit, a clearance, a review) earns its place only if what it catches is
worth more than what it costs to run. That needs two numbers per gate: how often it *fired* and how
often it *caught* something. This reporter derives what the existing logs can support and says plainly
what they cannot. It adds no logging duty: it only reads.

What it reads, all under ``--root``:

* ``references/security-decisions.log.csv`` — ``@security`` verdicts. Verdicts are logged as a rule, so
  this is the one gate with a usable denominator. HALT and CONDITIONAL verdicts are counted as catches,
  PASS-like ones as clean firings. The verdict is read from ``status`` (or legacy ``verdict``) and, when
  that cell holds bookkeeping (open, closed, cleared ...), from a verdict word leading ``decision``.
  Signed team ledgers under the agent directories are counted separately, never summed in.
* ``.github/agents/references/conflict-log.csv`` — ``@conflict-auditor`` findings (claimed catches).
  Clean audits are logged only sometimes, so this gate has **no reliable denominator**.
* ``tmp/by-week/<week>/*.steps.csv`` — plan steps that name a gate agent: a *lower bound* on firings.

**Privacy.** Those logs are local and unpublished. This script emits **counts only**, keyed by a closed
set of labels. No cell of any log — description, conditions, evidence, file, status or severity text —
is ever copied into the output. Keep the output itself in an untracked location all the same.

**Limits it reports rather than hides.** A logged catch is a *claim* (some audit findings are wrong).
A clean record may be caused by the gate (authors comply in advance). ``@adversarial`` reviews leave
no log row at all. Plan step files are scratch and are cleaned up, so old weeks are covered unevenly.
Classification is by leading verdict word and is approximate; unclassified rows are counted.

Run: ``python scripts/gate_catch_ledger.py [--root DIR] [--json] [--output FILE]``. Exit 0 whatever
the ledger shows (this reports; it does not gate); exit 1 when ``--output`` cannot be written; exit 2
on a usage error.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agentteams.plan_steps import read_steps  # noqa: E402

SECURITY_LOG = Path("references/security-decisions.log.csv")
#: Signed team ledgers. Reported separately: their rows may repeat verdicts in the main log.
TEAM_SECURITY_LOGS = (
    Path(".claude/agents/references/security-decisions.log.csv"),
    Path(".github/agents/references/security-decisions.log.csv"),
)
CONFLICT_LOG = Path(".github/agents/references/conflict-log.csv")
PLAN_GLOB = "tmp/by-week/*/*.steps.csv"

#: Gate agents whose plan-step mentions are counted as recorded firings.
GATE_AGENTS = (
    "adversarial",
    "conflict-auditor",
    "security",
    "code-hygiene",
    "technical-validator",
    "quality-auditor",
    "agent-updater",
    "work-summarizer",
)
_GATE_RES = {
    gate: re.compile(r"(?<![a-z-])" + gate.replace("-", "[-_ ]") + r"(?![a-z-])")
    for gate in GATE_AGENTS
}

VERDICT_CLASSES = ("halt", "conditional", "pass", "other")
SEVERITY_LABELS = {
    "critical": "critical", "blocking": "critical",
    "high": "high", "major": "high",
    "medium": "medium", "med": "medium", "moderate": "medium", "should-fix": "medium", "normal": "medium",
    "low": "low", "minor": "low",
    "info": "info", "informational": "info", "advisory": "info", "none": "info",
}
#: Conflict-log categories and codes that record an audit run or a note, not a finding.
NON_FINDING_CATEGORIES = frozenset({"NONE", "CONFLICT_AUDIT"})
NON_FINDING_CODES = frozenset({"OK"})

_CLEAN_RE = re.compile(r"\bclean[ _-]?audit\b|\bno (?:findings|conflicts)\b|\b0 findings\b", re.IGNORECASE)
_FALSE_RE = re.compile(
    r"\bno (?:action|change)(?: needed| required)?\b|\bfalse positive\b|\bnon-issue\b"
    r"|\bnot a (?:conflict|defect|finding)\b|\bwithdrawn\b|\bretracted\b",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"[\s_-]+")
_MONTH_RE = re.compile(r"^(\d{4}-\d{2})")
_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
_CONDITIONAL_RE = re.compile(
    r"^(?:CONDITIONAL|(?:PASS(?:ED)?|CLEAR(?:ED)?|APPROVED?|ALLOW(?:ED)?) (?:WITH|\(CONDITIONAL))"
)
_PASS_RE = re.compile(r"^(?:PASS(?:ED)?|CLEAR(?:ED)?|APPROVED?|ALLOW(?:ED)?|AUTHORIZATION CLEARED)(?![A-Z])")
_HALT_RE = re.compile(r"^(?:HALT(?:ED)?|BLOCKED|FAIL(?:ED)?)(?![A-Z])")
_BARE_CLEAR_RE = re.compile(r"^CLEAR(?:ED)?$")


def rule_of_three(clean_firings: int) -> float | None:
    """Return the 95% upper bound on a catch rate after ``clean_firings`` firings with no catch.

    Args:
        clean_firings: Number of observed firings, none of which caught anything.

    Returns:
        ``min(1.0, 3 / n)``, or ``None`` when ``n`` is not positive (no bound without observations).
    """
    if clean_firings <= 0:
        return None
    return min(1.0, 3.0 / clean_firings)


def _read_rows(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Read a CSV into dict rows.

    Returns:
        ``(state, rows)`` where ``state`` is ``found``, ``absent`` or ``unreadable``. An unreadable
        file still returns the rows read before the failure.
    """
    rows: list[dict[str, str]] = []
    if not path.exists():
        return "absent", rows
    try:
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                rows.append({k: (v or "") for k, v in row.items() if k})
    except (OSError, csv.Error):
        return "unreadable", rows
    return "found", rows


def _month(value: str) -> str:
    """Return the ``YYYY-MM`` prefix of a date cell, or ``unknown``."""
    match = _MONTH_RE.match(value.strip())
    return match.group(1) if match else "unknown"


def _verdict_word(cell: str) -> str | None:
    """Classify a cell by its leading verdict word; ``None`` when it does not lead with one."""
    text = _SPACE_RE.sub(" ", cell.strip().upper())
    if text.startswith("HALT RETRACT"):
        return None  # a retraction lifts a halt; it is not a catch
    if _HALT_RE.match(text):
        return "halt"
    if _CONDITIONAL_RE.match(text):
        return "conditional"
    if _PASS_RE.match(text):
        return "pass"
    return None


def classify_security_status(status: str, decision: str = "") -> str:
    """Map a security-log row to ``halt``, ``conditional``, ``pass`` or ``other``.

    Args:
        status: The row's ``status`` (or legacy ``verdict``) cell.
        decision: The row's ``decision`` cell. Used when ``status`` holds bookkeeping or a bare
            "cleared", so that a cleared row never hides a HALT or CONDITIONAL decision.

    Returns:
        The verdict class. Rows with no leading verdict word in either cell are ``other`` and are
        not counted as firings.
    """
    from_status = _verdict_word(status)
    bare_clear = bool(_BARE_CLEAR_RE.match(_SPACE_RE.sub(" ", status.strip().upper())))
    if from_status is None or bare_clear:
        from_decision = _verdict_word(decision)
        if from_decision in {"halt", "conditional"} or from_status is None:
            return from_decision or from_status or "other"
    return from_status or "other"


def security_ledger(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Summarise ``@security`` verdicts.

    Args:
        rows: Rows of a security decisions log (current or legacy schema).

    Returns:
        Counts by verdict class and month, the firing total (halt + conditional + pass) and
        the halt and conditional rates over that total (``None`` when there are no firings).
    """
    by_class: Counter[str] = Counter()
    by_month: dict[str, Counter[str]] = {}
    for row in rows:
        verdict = classify_security_status(row.get("status") or row.get("verdict") or "", row.get("decision", ""))
        by_class[verdict] += 1
        by_month.setdefault(_month(row.get("date") or row.get("timestamp") or ""), Counter())[verdict] += 1
    firings = by_class["halt"] + by_class["conditional"] + by_class["pass"]
    return {
        "rows": len(rows),
        "firings": firings,
        "halt": by_class["halt"],
        "conditional": by_class["conditional"],
        "pass": by_class["pass"],
        "unclassified": by_class["other"],
        "halt_rate": (by_class["halt"] / firings) if firings else None,
        "conditional_rate": (by_class["conditional"] / firings) if firings else None,
        "halt_rate_upper_bound_if_zero": rule_of_three(firings) if not by_class["halt"] else None,
        "by_month": {
            month: {name: counts[name] for name in VERDICT_CLASSES if counts[name]}
            for month, counts in sorted(by_month.items())
        },
    }


def _is_non_finding_row(row: dict[str, str]) -> bool:
    """True when a conflict-log row records a clean audit or an audit note instead of a finding."""
    category = row.get("category", "").strip().upper()
    code = row.get("code", "").strip().upper()
    if category.startswith("CLEAN") or code.startswith("CLEAN"):
        return True
    if category in NON_FINDING_CATEGORIES or code in NON_FINDING_CODES:
        return True
    return bool(_CLEAN_RE.search(row.get("description", "")))


def conflict_ledger(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Summarise ``@conflict-auditor`` log rows.

    Args:
        rows: Rows of the conflict log.

    Returns:
        Finding counts (claimed catches) by severity label, status label and month; the number of
        rows that record a clean audit or note; and the number of findings whose resolution reads
        as a false catch. Severity and status are mapped onto closed label sets.
    """
    severity: Counter[str] = Counter()
    status: Counter[str] = Counter()
    month: Counter[str] = Counter()
    non_findings = 0
    false_catches = 0
    for row in rows:
        if _is_non_finding_row(row):
            non_findings += 1
            continue
        severity[SEVERITY_LABELS.get(row.get("severity", "").strip().lower(), "other")] += 1
        state = row.get("status", "").strip().lower()
        status["closed" if state in {"resolved", "closed"} else "open" if state == "open" else "other"] += 1
        month[_month(row.get("date", ""))] += 1
        if _FALSE_RE.search(row.get("resolution", "")):
            false_catches += 1
    return {
        "rows": len(rows),
        "claimed_catches": sum(severity.values()),
        "clean_audit_rows": non_findings,
        "resolved_as_false": false_catches,
        "by_severity": dict(sorted(severity.items())),
        "by_status": dict(sorted(status.items())),
        "by_month": dict(sorted(month.items())),
        "firings": None,
    }


def gates_named(agent_cell: str) -> set[str]:
    """Return the gate agents an ``agent`` cell names, tolerating spaces, underscores and capitals.

    Args:
        agent_cell: The raw ``agent`` cell of a plan step.

    Returns:
        The subset of :data:`GATE_AGENTS` named in the cell.
    """
    text = agent_cell.lower()
    return {gate for gate, pattern in _GATE_RES.items() if pattern.search(text)}


def plan_ledger(root: Path) -> dict[str, Any]:
    """Count plan steps that name a gate agent, per ISO week.

    Args:
        root: Repository root holding ``tmp/by-week``.

    Returns:
        Weeks covered, plan and step totals, per-gate step counts (a lower bound on firings), the
        per-week plan count, and how many files were unreadable or had malformed rows.
    """
    per_gate: Counter[str] = Counter()
    per_week: Counter[str] = Counter()
    steps = plans = unreadable = malformed = 0
    for path in sorted(root.glob(PLAN_GLOB)):
        week = path.parent.name
        if not _WEEK_RE.match(week):
            continue
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                rows = read_steps(path)
        except (OSError, UnicodeDecodeError, csv.Error):
            unreadable += 1
            continue
        malformed += bool(caught)
        plans += 1
        per_week[week] += 1
        steps += len(rows)
        for row in rows:
            for gate in gates_named(row.get("agent", "")):
                per_gate[gate] += 1
    return {
        "weeks_covered": sorted(per_week),
        "plans": plans,
        "steps": steps,
        "unreadable_files": unreadable,
        "files_with_malformed_rows": malformed,
        "gate_steps": {gate: per_gate[gate] for gate in GATE_AGENTS},
        "plans_by_week": dict(sorted(per_week.items())),
    }


def build_ledger(root: Path) -> dict[str, Any]:
    """Build the whole ledger for one repository root.

    Args:
        root: Repository root to read the logs from.

    Returns:
        A JSON-serialisable dict with ``coverage``, ``security``, ``team_security_ledgers``,
        ``conflict_auditor`` and ``plans`` sections. A log that is absent or yields no rows gives
        ``None`` for its section; its state is in ``coverage``.
    """
    security_state, security_rows = _read_rows(root / SECURITY_LOG)
    conflict_state, conflict_rows = _read_rows(root / CONFLICT_LOG)
    team_ledgers = []
    for index, rel in enumerate(TEAM_SECURITY_LOGS, start=1):
        state, rows = _read_rows(root / rel)
        if state != "absent":
            summary = security_ledger(rows)
            team_ledgers.append({"ledger": index, "state": state, **{k: summary[k] for k in (
                "rows", "firings", "halt", "conditional", "pass", "unclassified")}})
    plans = plan_ledger(root)
    without = ["conflict-auditor"] + [g for g in GATE_AGENTS if g not in {"security", "conflict-auditor"}]
    if not security_rows:
        without.insert(0, "security")
    coverage = {
        "generated": datetime.date.today().isoformat(),
        "security_log": security_state,
        "conflict_log": conflict_state,
        "team_security_ledgers_found": len(team_ledgers),
        "plan_weeks": plans["weeks_covered"],
        "gates_with_denominator": ["security"] if security_rows else [],
        "gates_without_denominator": without,
        "notes": [
            "A logged catch is a claim; some audit findings are wrong.",
            "Security verdicts are classed by leading verdict word; unclassified rows are not firings.",
            "Signed team security ledgers are counted separately and may repeat rows of the main log.",
            "Clean conflict audits are logged inconsistently: no catch rate can be computed for that gate.",
            "Plan-step mentions are a lower bound on firings; reviews not written as a step are invisible.",
            "Plan step files are scratch and are cleaned up; older weeks are covered unevenly.",
            "Do not retire a gate on absence of catches alone: first show it still fires on a seeded defect.",
        ],
    }
    return {
        "coverage": coverage,
        "security": security_ledger(security_rows) if security_rows else None,
        "team_security_ledgers": team_ledgers,
        "conflict_auditor": conflict_ledger(conflict_rows) if conflict_rows else None,
        "plans": plans,
    }


def _pct(value: float | None) -> str:
    """Format a rate as a percentage, or ``n/a``."""
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _pairs(counts: dict[str, int]) -> str:
    """Render a label-count mapping on one line, or ``none``."""
    return ", ".join(f"{label} {count}" for label, count in counts.items()) or "none"


def render_markdown(ledger: dict[str, Any]) -> str:
    """Render a ledger as Markdown.

    Args:
        ledger: The dict returned by :func:`build_ledger`.

    Returns:
        A Markdown report containing counts and fixed labels only.
    """
    out: list[str] = ["# Gate catch ledger", ""]
    cov = ledger["coverage"]
    weeks = cov["plan_weeks"]
    out += ["## Coverage", "", f"- Generated: {cov['generated']}"]
    out.append(f"- Security decisions log: {cov['security_log'].upper()}")
    out.append(f"- Conflict log: {cov['conflict_log'].upper()}")
    out.append(f"- Signed team security ledgers found: {cov['team_security_ledgers_found']}")
    out.append(f"- Plan step files: {len(weeks)} week(s)" + (f", {weeks[0]} to {weeks[-1]}" if weeks else ""))
    out.append(f"- Gates with a firing count: {', '.join(cov['gates_with_denominator']) or 'none'}")
    out.append(f"- Gates without a denominator: {', '.join(cov['gates_without_denominator'])}")
    out += [f"- {note}" for note in cov["notes"]]
    out.append("")

    sec = ledger["security"]
    out += ["## @security (verdicts are logged as a rule: usable denominator)", ""]
    if sec is None:
        out += ["No data.", ""]
    else:
        out += [
            "| Firings | HALT | CONDITIONAL | PASS | HALT rate | CONDITIONAL rate | Unclassified rows |",
            "|---|---|---|---|---|---|---|",
            f"| {sec['firings']} | {sec['halt']} | {sec['conditional']} | {sec['pass']} | "
            f"{_pct(sec['halt_rate'])} | {_pct(sec['conditional_rate'])} | {sec['unclassified']} |",
            "",
        ]
        if sec["halt_rate_upper_bound_if_zero"] is not None:
            bound = _pct(sec["halt_rate_upper_bound_if_zero"])
            out += [f"No HALT observed; rule-of-three upper bound on the HALT rate: {bound}.", ""]
        out += ["| Month | HALT | CONDITIONAL | PASS |", "|---|---|---|---|"]
        for month, counts in sec["by_month"].items():
            out.append(
                f"| {month} | {counts.get('halt', 0)} | {counts.get('conditional', 0)} | {counts.get('pass', 0)} |"
            )
        out.append("")
    for team in ledger["team_security_ledgers"]:
        out.append(
            f"- Signed team ledger {team['ledger']} ({team['state']}): {team['rows']} row(s); "
            f"HALT {team['halt']}, CONDITIONAL {team['conditional']}, PASS {team['pass']}, "
            f"unclassified {team['unclassified']}"
        )
    if ledger["team_security_ledgers"]:
        out.append("")

    con = ledger["conflict_auditor"]
    out += ["## @conflict-auditor (no denominator)", ""]
    if con is None:
        out += ["No data.", ""]
    else:
        out += [
            f"- Claimed catches (finding rows): {con['claimed_catches']}",
            f"- Rows recording a clean audit or an audit note: {con['clean_audit_rows']}",
            f"- Findings whose resolution reads as a false catch or no action: {con['resolved_as_false']}",
            f"- By severity: {_pairs(con['by_severity'])}",
            f"- By status: {_pairs(con['by_status'])}",
            f"- By month: {_pairs(con['by_month'])}",
            "",
        ]

    plans = ledger["plans"]
    out += ["## Plan steps naming a gate agent (lower bound on firings)", ""]
    out.append(
        f"{plans['plans']} plan(s), {plans['steps']} step(s), {plans['unreadable_files']} unreadable file(s), "
        f"{plans['files_with_malformed_rows']} file(s) with malformed rows."
    )
    out += ["", "| Gate | Steps naming it | Per plan |", "|---|---|---|"]
    for gate, count in plans["gate_steps"].items():
        per_plan = f"{count / plans['plans']:.2f}" if plans["plans"] else "n/a"
        out.append(f"| @{gate} | {count} | {per_plan} |")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        0 whatever the ledger shows; 1 when ``--output`` cannot be written.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT,
                        help="Repository root holding the logs (default: this script's repository).")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write the report here instead of printing it. Use an untracked location.")
    args = parser.parse_args(argv)
    ledger = build_ledger(args.root)
    text = json.dumps(ledger, indent=2, sort_keys=True) if args.json else render_markdown(ledger)
    if args.output is None:
        print(text)
        return 0
    try:
        args.output.write_text(text + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"gate_catch_ledger: could not write {args.output}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
