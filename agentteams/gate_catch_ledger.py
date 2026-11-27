"""Derive a per-gate catch ledger from the governance logs a team already keeps.

**Why.** A process gate (an audit, a clearance, a review) earns its place only if what it catches is
worth more than what it costs to run. That needs two numbers per gate: how often it *fired* and how
often it *caught* something. This reporter derives what the existing logs can support and says plainly
what they cannot. It adds no logging duty: it only reads.

What it reads:

* the team's security decisions log, ``<team dir>/references/security-decisions.log.csv`` (or the file
  given with ``--security-log``) — ``@security`` verdicts. Verdicts are logged as a rule, so this is the
  one gate with a usable denominator. HALT and CONDITIONAL verdicts are counted as catches, PASS-like
  ones as clean firings. The verdict is read from ``status`` (or ``verdict``) and, when that cell holds
  bookkeeping (open, closed, cleared ...), from a verdict word leading ``decision``.
* the team's conflict log, ``<team dir>/references/conflict-log.csv`` — ``@conflict-auditor`` findings
  (claimed catches). Clean audits are logged only sometimes, so this gate has **no reliable denominator**.
* the gitignored plan-step files under ``<root>/tmp/by-week/<week>/`` — steps that name a gate agent: a
  *lower bound* on firings.

The team directory is the framework's agents directory (``.claude/agents``, ``.github/agents`` ...). It
is taken from ``--team-dir`` or ``--framework``; with neither, it is detected, and a repository holding
more than one team is refused instead of guessed.

**Privacy.** Those logs are local and unpublished. This module emits **counts only**, keyed by a closed
set of labels. No cell of any log — description, conditions, evidence, file, status or severity text —
is ever copied into the output. Keep the output itself in an untracked location all the same.

**Limits it reports rather than hides.** A logged catch is a *claim* (some audit findings are wrong).
A clean record may be caused by the gate (authors comply in advance). ``@adversarial`` reviews leave
no log row at all. Plan step files are scratch and are cleaned up, so old weeks are covered unevenly.
A re-recorded verdict counts as another firing. Classification is by leading verdict word and is
approximate; unclassified rows are counted. A source that is absent is reported as absent, never as zero.

Run: ``python -m agentteams.gate_catch_ledger [--root DIR] [--framework ID | --team-dir DIR]
[--security-log FILE] [--json] [--output FILE]``. Exit 0 whatever the ledger shows (this reports; it
does not gate); exit 1 when ``--output`` cannot be written; exit 2 on a usage error, including a
repository with more than one team and no ``--framework`` or ``--team-dir``.
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

from agentteams.liaison_logs import CONFLICT_LOG_CSV, SECURITY_DECISIONS_CSV
from agentteams.plan_steps import read_steps

#: Log locations inside a team directory (the framework's agents directory).
SECURITY_LOG = Path("references") / SECURITY_DECISIONS_CSV
CONFLICT_LOG = Path("references") / CONFLICT_LOG_CSV
PLAN_GLOB = "tmp/by-week/*/*.steps.csv"  # gitignored plan-step files

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
    if _SPACE_RE.sub(" ", status.strip().upper()).startswith("HALT RETRACT"):
        return "other"  # a retraction row is never a firing, whatever its decision cell says
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
        root: Repository root holding the plan-step files (``tmp/by-week``).

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


def resolve_team_dir(root: Path, framework: str | None = None, team_dir: Path | None = None) -> Path | None:
    """Find the team directory whose logs the ledger reads.

    Args:
        root: Repository root.
        framework: A registered framework id; its agents directory is used.
        team_dir: An explicit team directory (absolute, or relative to ``root``). Wins over ``framework``.

    Returns:
        The team directory, or ``None`` when neither argument is given and no team with a governance
        log is found under ``root``.

    Raises:
        ValueError: ``framework`` is not registered; the directory an argument names does not exist;
            or neither argument is given and more than one team directory under ``root`` holds a
            governance log. The message names directories relative to ``root``, never an absolute path.
    """
    from agentteams.frameworks import registry  # deferred: it loads every framework adapter

    if team_dir is not None or framework is not None:
        chosen = registry.agents_dir_for(framework, root) if team_dir is None else (
            team_dir if team_dir.is_absolute() else root / team_dir)
        if not chosen.is_dir():
            shown = chosen.relative_to(root).as_posix() if chosen.is_relative_to(root) else chosen.name
            raise ValueError(f"team directory not found: {shown}")
        return chosen
    found: list[Path] = []
    for name in registry.FRAMEWORKS:
        candidate = registry.agents_dir_for(name, root)
        if candidate in found:
            continue
        if (candidate / SECURITY_LOG).exists() or (candidate / CONFLICT_LOG).exists():
            found.append(candidate)
    if len(found) > 1:
        names = ", ".join(sorted(path.relative_to(root).as_posix() for path in found))
        raise ValueError(
            f"more than one team directory holds a governance log ({names}); pass --framework or --team-dir"
        )
    return found[0] if found else None


def build_ledger(root: Path, team_dir: Path | None = None, security_log: Path | None = None) -> dict[str, Any]:
    """Build the whole ledger for one repository and team.

    Args:
        root: Repository root (plan-step files are read from here).
        team_dir: The team directory holding ``references/``; ``None`` when the repository has no team.
        security_log: A security decisions log to read instead of the team directory's own.

    Returns:
        A JSON-serialisable dict with ``coverage``, ``security``, ``conflict_auditor`` and ``plans``
        sections. A source that is absent, unreadable or yields nothing countable gives ``None`` for
        its section (no denominator, never zero); its state is in ``coverage``.
    """
    absent: tuple[str, list[dict[str, str]]] = ("absent", [])
    if security_log is not None:
        security_state, security_rows = _read_rows(security_log)
    else:
        security_state, security_rows = _read_rows(team_dir / SECURITY_LOG) if team_dir else absent
    conflict_state, conflict_rows = _read_rows(team_dir / CONFLICT_LOG) if team_dir else absent
    plans = plan_ledger(root)
    security = security_ledger(security_rows) if security_state == "found" and security_rows else None
    if security is not None and not security["firings"]:
        security = None  # rows but no recognisable verdict: no denominator
    conflict = conflict_ledger(conflict_rows) if conflict_state == "found" and conflict_rows else None
    without = ["conflict-auditor"] + [g for g in GATE_AGENTS if g not in {"security", "conflict-auditor"}]
    if security is None:
        without.insert(0, "security")
    coverage = {
        "generated": datetime.date.today().isoformat(),
        "team_dir_found": team_dir is not None,
        "security_log": security_state,
        "conflict_log": conflict_state,
        "plan_steps": "found" if plans["plans"] else ("unreadable" if plans["unreadable_files"] else "absent"),
        "plan_weeks": plans["weeks_covered"],
        "gates_with_denominator": ["security"] if security is not None else [],
        "gates_without_denominator": without,
        "notes": [
            "A logged catch is a claim; some audit findings are wrong.",
            "Security verdicts are classed by leading verdict word; unclassified rows are not firings.",
            "A re-recorded security verdict counts as another firing.",
            "Clean conflict audits are logged inconsistently: no catch rate can be computed for that gate.",
            "Plan-step mentions are a lower bound on firings; reviews not written as a step are invisible.",
            "Plan step files are scratch and are cleaned up; older weeks are covered unevenly.",
            "A source reported absent means no denominator, not zero firings.",
            "Do not retire a gate on absence of catches alone: first show it still fires on a seeded defect.",
        ],
    }
    return {
        "coverage": coverage,
        "security": security,
        "conflict_auditor": conflict,
        "plans": plans if plans["plans"] else None,
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
    out.append(f"- Team directory: {'found' if cov['team_dir_found'] else 'NOT FOUND'}")
    plan_state = f"{len(weeks)} week(s), {weeks[0]} to {weeks[-1]}" if weeks else cov["plan_steps"].upper()
    out.append(f"- Plan step files: {plan_state}")
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
    if plans is None:
        out += ["No readable plan step files found: no denominator.", ""]
        return "\n".join(out)
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
        0 whatever the ledger shows; 1 when ``--output`` cannot be written; 2 on a usage error.
    """
    parser = argparse.ArgumentParser(prog="python -m agentteams.gate_catch_ledger",
                                     description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."),
                        help="Repository root (default: the current directory).")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--framework", default=None,
                       help="Framework id whose agents directory holds the team's logs (e.g. claude).")
    where.add_argument("--team-dir", type=Path, default=None,
                       help="The team directory holding references/ (absolute, or relative to --root).")
    parser.add_argument("--security-log", type=Path, default=None,
                        help="Read this security decisions log instead of the team directory's own.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write the report here instead of printing it. Use an untracked location.")
    args = parser.parse_args(argv)
    try:
        team_dir = resolve_team_dir(args.root, args.framework, args.team_dir)
    except ValueError as exc:
        print(f"gate_catch_ledger: {exc}", file=sys.stderr)
        return 2
    ledger = build_ledger(args.root, team_dir, args.security_log)
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
