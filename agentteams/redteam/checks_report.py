"""checks_report.py — phase 6, checks F-4 to F-6: the ones that read reports and ledgers.

* **F-4 — a coverage claim with an unexamined denominator.** The 2026-08-06 report said *"0
  agents on the ignored key across the fleet."* Arithmetically true. The denominator was 15
  repositories out of 85 workspaces; the real figure was 719 exposed agents in 34 untouched
  teams, and it surfaced only because the operator asked a follow-up question.
* **F-5 — a landing control can make a probe start measuring the wrong thing.** Two probes
  flipped to a false ``DEFENDED`` after their fix shipped: ``B10`` was skipping the *genuine*
  backup root, and ``A9`` named an obviously-fake waiver approver that the new roster check
  rejected — hiding that T2 is still undefended. Both were made *stricter*. Re-running probes
  does not catch this; re-validating probe *intent* does.
* **F-6 — accepting a weakness must cost a diff.** A named reason per non-``DEFENDED`` probe,
  and a separate check that a probe which now defends loses its exemption.

**F-4 reads two scopes, and only one of them gates.** The *gating* scope is this run's rendered
artifacts. The *advisory* scope is the hand-written red-team documents under
``references/plans/`` — where the original failure actually happened — reported in full but not
blocking, because gating on immutable historical documents leaves the job permanently red and a
permanently red job is one nobody reads.

The obvious objection to the gating scope is that a check reading only what the same program
wrote passes by construction. It does not: the renderer emits *prose* as well as its Counts
table, a bare count added to that prose fires the check, and the fires-tests in
``tests/test_redteam_selfaudit.py`` feed it hand-written text — including the literal
2026-08-06 sentence — to prove the alarm rings on input it did not author.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentteams.redteam.registry import (
    ACCEPTED_WEAKNESSES_REL,
    CANONICAL_POPULATION_SOURCES,
    DEFENDED,
    MIN_REASON_CHARS,
    PROBE_BASELINE_REL,
    Probe,
    SelfAuditFinding,
    evidence_digest,
    load_accepted_weaknesses,
)

#: The command that accepts a changed probe outcome. Printed in every F-5 finding, because a
#: remedy that names a command gets done and one that says "investigate" gets deferred.
ACCEPT_BASELINE_COMMAND = "agentteams --redteam --accept-probe-baseline"

#: Columns that mark a markdown table as a Counts table.
_COUNTS_COLUMNS = ("claim", "numerator", "denominator", "population_source")

#: Nouns that turn a bare number into a coverage claim. Each is a population someone counted
#: on 2026-08-06 without saying what they divided by.
_POPULATION_NOUNS = (
    "agent", "agents", "repo", "repos", "repositories", "workspace", "workspaces",
    "team", "teams", "probe", "probes", "exploit", "exploits", "finding", "findings",
    "file", "files", "module", "modules",
)

#: A claim-shaped numeral: an integer, then up to a few words, then a population noun.
_CLAIM_RE = re.compile(
    r"\b(\d+)\b(?:\s+\w+){0,3}\s+(" + "|".join(_POPULATION_NOUNS) + r")\b",
    re.IGNORECASE,
)

#: Markers that show a denominator is present on the line. ``N of M``, an explicit
#: ``population:``, a percentage with its base, or a fraction.
_DENOMINATOR_RE = re.compile(
    r"\bof\s+\d+\b|population\s*[:=]|denominator|\b\d+\s*/\s*\d+\b|\bout of\b",
    re.IGNORECASE,
)

#: Spans that are not prose claims: dates, section references, version numbers, line anchors,
#: constitutional article ids, tier ids, remediation ids, failure-mode ids, and phase numbers.
#: "Phase 6 evaluates the red team" is an ordinal, not a count of six things.
_NOT_A_CLAIM_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}|§\s*\d|\bv?\d+\.\d+|:\d+\b|\bC-\d\b|\bT\d\b|\bW\d+\b|\bF-\d\b"
)

#: Words that make a preceding numeral an ordinal rather than a count.
_ORDINAL_CONTEXT_RE = re.compile(r"\b(phase|step|rule|tier|article|section|item)\s+\d+\b", re.I)


def _report_documents(
    root: Path, report_dir: Path, rendered: dict[str, str] | None, *, historical: bool
) -> dict[str, list[str]]:
    """Return ``label -> lines`` for the documents F-4 reads.

    ``rendered`` carries this run's artifacts **in memory** rather than by reading them back
    off disk. Two properties follow that a read-back could not give: the check works
    identically under ``--dry-run`` (where nothing is written), and it does not depend on
    phase 6 running after the artifacts land.

    ``historical`` selects the *advisory* sweep over hand-written red-team documents under
    ``references/plans/``. Split from the gating sweep deliberately. The handoff's own wording
    is that *"a finding report that states a numerator without its denominator must fail its
    own audit gate"* — the report being produced, not every report ever written. Gating on
    immutable historical documents would leave the daily job permanently red, which trains a
    reader to ignore red, and would catch nothing new on any subsequent day. The findings are
    still emitted, under their own heading, so nothing is hidden.

    **Prior runs' artifacts in ``report_dir`` are deliberately NOT read back.** They were
    checked when they were produced, so re-reading adds nothing — and it creates a feedback
    loop that showed up on the very first consecutive pair of runs: day two read day one's
    ``selfaudit.md``, whose *narrative* ("Phase 6 evaluates the red team") tripped the
    claim-shaped-numeral rule. A check whose input includes its own previous output
    accumulates noise about itself, which is the fastest route to being muted.
    """
    documents: dict[str, list[str]] = {}
    if historical:
        plans = root / "references" / "plans"
        if plans.is_dir():
            for path in sorted(p for p in plans.glob("*.md") if "redteam" in p.name):
                documents[path.name] = path.read_text(encoding="utf-8").splitlines()
        return documents
    for name, text in (rendered or {}).items():
        documents[name] = text.splitlines()
    return documents


def _counts_table_findings(label: str, lines: list[str]) -> list[SelfAuditFinding]:
    """Validate every Counts table in a document."""
    findings: list[SelfAuditFinding] = []
    header_index: int | None = None
    for index, line in enumerate(lines):
        cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
        if all(column in cells for column in _COUNTS_COLUMNS):
            header_index = index
            source_column = cells.index("population_source")
            claim_column = cells.index("claim")
            for row in lines[index + 2:]:
                if not row.lstrip().startswith("|"):
                    break
                values = [c.strip() for c in row.strip().strip("|").split("|")]
                if len(values) <= max(source_column, claim_column):
                    continue
                source = values[source_column].strip("`")
                if source not in CANONICAL_POPULATION_SOURCES:
                    findings.append(SelfAuditFinding(
                        check="F-4",
                        subject=f"{label}: {values[claim_column]}",
                        detail=(
                            f"population source {source!r} is not a canonical enumerator"
                        ),
                        remedy=(
                            "compute the population with one of "
                            f"{sorted(CANONICAL_POPULATION_SOURCES)}; an ad-hoc list is how a "
                            "denominator goes unexamined"
                        ),
                    ))
    if header_index is None and any(_CLAIM_RE.search(line) for line in lines):
        findings.append(SelfAuditFinding(
            check="F-4",
            subject=label,
            detail="document makes counted claims but carries no Counts table",
            remedy=(
                "add a table with columns claim | numerator | denominator | population_source"
            ),
        ))
    return findings


def check_counts_have_population(
    root: Path, report_dir: Path, *, rendered: dict[str, str] | None = None,
    historical: bool = False,
) -> list[SelfAuditFinding]:
    """F-4: every count is stated with the population it was computed over.

    Args:
        root: Repository root.
        report_dir: Directory holding previously emitted artifacts.
        rendered: This run's artifacts, in memory, as ``filename -> text``.
        historical: Sweep hand-written red-team documents under ``references/plans/`` instead
            of this run's artifacts. Advisory; see :func:`_report_documents`.

    Returns:
        One finding per bare claim, non-canonical population source, or document that counts
        without a Counts table. **A scope that resolves to zero documents is itself a
        finding** — a check with nothing to read reports clean, which is indistinguishable
        from a check that works. The historical sweep is exempt from that rule: a project with
        no hand-written red-team documents is not a project with a broken check.
    """
    documents = _report_documents(root, report_dir, rendered, historical=historical)
    if not documents:
        if historical:
            return []
        return [SelfAuditFinding(
            check="F-4",
            subject=str(report_dir),
            detail="no report documents in scope — the check read nothing and would pass",
            remedy="run the attack phase first, or point --redteam-report at the artifacts",
        )]
    findings: list[SelfAuditFinding] = []
    for label, lines in documents.items():
        findings.extend(_counts_table_findings(label, lines))
        in_fence = False
        for number, line in enumerate(lines, start=1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or line.lstrip().startswith("|"):
                continue
            match = _CLAIM_RE.search(line)
            if not match:
                continue
            if (_DENOMINATOR_RE.search(line)
                    or _NOT_A_CLAIM_RE.search(match.group(0))
                    or _ORDINAL_CONTEXT_RE.search(line)):
                continue
            findings.append(SelfAuditFinding(
                check="F-4",
                subject=f"{label}:{number}",
                detail=f"claims {match.group(0)!r} with no denominator on the line",
                remedy=(
                    "state it as `N of M (population: <canonical enumerator>)` — a numerator "
                    "without its denominator is the failure that hid 719 exposed agents"
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# F-5 — probe intent re-validation
# ---------------------------------------------------------------------------

def load_probe_baseline(root: Path) -> dict[str, dict[str, str]]:
    """Load the committed probe baseline.

    Returns:
        ``pid -> {"outcome": ..., "evidence_digest": ..., "note": ...}``, or ``{}`` when no
        baseline exists.
    """
    path = root / PROBE_BASELINE_REL
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["probes"]


def build_probe_baseline(probes: list[Probe]) -> dict:
    """Render the baseline document for a set of probe results.

    Args:
        probes: Probe results from the current run.

    Returns:
        A JSON-serialisable baseline. Written **only** by the explicit
        ``--accept-probe-baseline`` command: a daily job that re-baselines itself overnight
        silently absorbs every probe whose meaning drifted, which is F-5 with extra steps.
    """
    return {
        "schema_version": 1,
        "probes": {
            probe.pid: {
                "outcome": probe.outcome,
                "evidence_digest": evidence_digest(probe),
                "note": "",
            }
            for probe in sorted(probes, key=lambda p: p.pid)
        },
    }


def check_probe_intent(root: Path, probes: list[Probe]) -> list[SelfAuditFinding]:
    """F-5: a probe whose behaviour changed is re-validated for intent, not just re-run.

    A probe can start passing because the control got better or because the probe got blinder.
    Re-running cannot tell those apart; comparing against a committed baseline and demanding a
    human note can.

    Args:
        root: Repository root.
        probes: Probe results from the current run.

    Returns:
        One finding per probe whose outcome or normalised evidence changed without an
        accompanying baseline note, plus one per stale baseline entry.
    """
    if not probes:
        return []
    baseline = load_probe_baseline(root)
    if not baseline:
        return [SelfAuditFinding(
            check="F-5",
            subject=PROBE_BASELINE_REL,
            detail=(
                f"{len(probes)} probes ran with no committed baseline, so no outcome change "
                f"can be detected"
            ),
            remedy=f"seed it once with `{ACCEPT_BASELINE_COMMAND}` and commit the result",
        )]
    findings: list[SelfAuditFinding] = []
    seen: set[str] = set()
    for probe in probes:
        seen.add(probe.pid)
        previous = baseline.get(probe.pid)
        if previous is None:
            findings.append(SelfAuditFinding(
                check="F-5",
                subject=probe.pid,
                detail="new probe with no baseline entry — its intent has never been recorded",
                remedy=f"review what it measures, then run `{ACCEPT_BASELINE_COMMAND}`",
            ))
            continue
        current_digest = evidence_digest(probe)
        outcome_changed = previous.get("outcome") != probe.outcome
        evidence_changed = previous.get("evidence_digest") != current_digest
        if not outcome_changed and not evidence_changed:
            continue
        if previous.get("note", "").strip():
            continue
        if outcome_changed:
            detail = (
                f"outcome changed {previous.get('outcome')} → {probe.outcome} with no "
                f"re-validation note"
            )
        else:
            detail = (
                "outcome held at "
                f"{probe.outcome} but the evidence changed — the probe may now be measuring "
                "something else"
            )
        findings.append(SelfAuditFinding(
            check="F-5", subject=probe.pid, detail=detail,
            remedy=(
                f"confirm the probe still attacks what it claims to, add a note saying so, "
                f"then run `{ACCEPT_BASELINE_COMMAND}`"
            ),
        ))
    for pid in sorted(set(baseline) - seen):
        findings.append(SelfAuditFinding(
            check="F-5",
            subject=pid,
            detail="baseline names a probe that no longer runs",
            remedy=f"remove it with `{ACCEPT_BASELINE_COMMAND}` after confirming the removal",
        ))
    return findings


# ---------------------------------------------------------------------------
# F-6 — accepted weaknesses
# ---------------------------------------------------------------------------

def check_accepted_weaknesses(root: Path, probes: list[Probe]) -> list[SelfAuditFinding]:
    """F-6: every accepted weakness is named, and every stale acceptance is removed.

    Args:
        root: Repository root.
        probes: Probe results from the current run.

    Returns:
        One finding per unaccepted weakness, mismatched outcome, thin reason, or stale
        exemption.
    """
    if not probes:
        return []
    accepted = load_accepted_weaknesses(root)
    findings: list[SelfAuditFinding] = []
    by_pid = {probe.pid: probe for probe in probes}

    for probe in probes:
        if probe.is_control or probe.outcome == DEFENDED:
            continue
        row = accepted.get(probe.pid)
        if row is None:
            findings.append(SelfAuditFinding(
                check="F-6",
                subject=probe.pid,
                detail=f"returned {probe.outcome} with no entry in the accepted-weakness ledger",
                remedy=(
                    f"fix it, or add a row to {ACCEPTED_WEAKNESSES_REL} stating what residue "
                    f"is being accepted and why"
                ),
            ))
            continue
        outcome, reason = row
        if outcome != probe.outcome:
            findings.append(SelfAuditFinding(
                check="F-6",
                subject=probe.pid,
                detail=f"ledger accepts {outcome}, probe returned {probe.outcome}",
                remedy=f"a changed outcome needs a changed reason in {ACCEPTED_WEAKNESSES_REL}",
            ))
        if len(reason) < MIN_REASON_CHARS:
            findings.append(SelfAuditFinding(
                check="F-6",
                subject=probe.pid,
                detail=f"accepted with a {len(reason)}-character reason; the bar is {MIN_REASON_CHARS}",
                remedy=f"state the residue being accepted in {ACCEPTED_WEAKNESSES_REL}",
            ))

    for pid in sorted(accepted):
        probe = by_pid.get(pid)
        if probe is None:
            findings.append(SelfAuditFinding(
                check="F-6",
                subject=pid,
                detail="accepted-weakness row names a probe that no longer runs",
                remedy=f"remove the stale row from {ACCEPTED_WEAKNESSES_REL}",
            ))
        elif probe.outcome == DEFENDED:
            findings.append(SelfAuditFinding(
                check="F-6",
                subject=pid,
                detail="probe now DEFENDs but keeps its exemption",
                remedy=(
                    f"remove the row from {ACCEPTED_WEAKNESSES_REL} so the ledger keeps "
                    f"describing the system"
                ),
            ))
    return findings


__all__ = [
    "ACCEPT_BASELINE_COMMAND",
    "build_probe_baseline",
    "check_accepted_weaknesses",
    "check_counts_have_population",
    "check_probe_intent",
    "load_probe_baseline",
]
