"""selfaudit.py — phase 6: evaluate the red team, not the target.

Phases 1–5 measure whether the constitution holds. Phase 6 measures whether *the measurement*
holds. It is the phase the 2026-08-06 session did not have, and every one of its six checks
exists because that session shipped something that looked like a control and was not:

======= =============================================================================
F-1     a verifier whose output could not change — ``digest(payload) == digest({})``
F-2     a fix attached to one of two call paths; 27 of 31 agents migrated, run reported success
F-3     three hand-rolled resolvers; one missed 34 workspaces and 719 exposed agents
F-4     a coverage claim over 15 repositories presented as a claim about the fleet
F-5     two probes that flipped to a false ``DEFENDED`` because they got blinder, not better
F-6     accepting a weakness without it costing a diff
======= =============================================================================

**Every check must be able to fail.** Each has a test that feeds it a defective fixture and
asserts it fires, and a second that feeds it a clean fixture and asserts it stays silent. A
self-audit that cannot fail is F-1 wearing a different hat, and it would be the most expensive
possible thing to ship here — a green light nobody can distinguish from a working one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentteams.redteam import checks_report, checks_static
from agentteams.redteam.registry import Probe, SelfAuditFinding

#: The six checks, in the order the handoff states them. The registry is a module constant so
#: :func:`run_selfaudit` cannot quietly run five of six — a check dropped from a hand-written
#: call sequence is invisible; one dropped from this tuple changes a diff and a count.
CHECK_IDS: tuple[str, ...] = ("F-1", "F-2", "F-3", "F-4", "F-5", "F-6")

CHECK_TITLES: dict[str, str] = {
    "F-1": "every verifier has a sensitivity test and a negative control",
    "F-2": "every fix reaches every call path",
    "F-3": "no hand-rolled target, descriptor or VCS resolution",
    "F-4": "every count carries the population it was computed over",
    "F-5": "every probe whose behaviour changed was re-validated for intent",
    "F-6": "every accepted weakness has a named reason",
}


@dataclass
class SelfAuditResult:
    """The phase-6 verdict.

    Attributes:
        findings: Every defect found, across all six checks.
        checks_run: The check ids that executed. A check that could not run (no probes, no
            report dir) is absent here rather than silently counted as clean.
        checks_skipped: ``check id -> why``. Kept explicit so a report can never present a
            skipped check as a passing one.
    """

    findings: list[SelfAuditFinding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    checks_skipped: dict[str, str] = field(default_factory=dict)
    advisory: list[SelfAuditFinding] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True when every check ran and none found anything.

        Advisory findings do not affect this. They are reported in full and worked off
        deliberately; gating on immutable historical documents would leave the daily job
        permanently red, and a permanently red job is one nobody reads.
        """
        return not self.findings and not self.checks_skipped

    def by_check(self) -> dict[str, list[SelfAuditFinding]]:
        """Group findings by check id, preserving :data:`CHECK_IDS` order."""
        grouped: dict[str, list[SelfAuditFinding]] = {cid: [] for cid in CHECK_IDS}
        for finding in self.findings:
            grouped.setdefault(finding.check, []).append(finding)
        return grouped


def run_selfaudit(
    root: Path, *, probes: list[Probe], report_dir: Path,
    rendered: dict[str, str] | None = None,
) -> SelfAuditResult:
    """Run all six phase-6 checks.

    Args:
        root: Repository root.
        probes: Probe results from phase 1, or ``[]`` when no probe module was registered.
        report_dir: Directory holding previously emitted artifacts, read by F-4.
        rendered: This run's artifacts in memory, so F-4 works identically under
            ``--dry-run`` and does not depend on phase 6 running after the write.

    Returns:
        The :class:`SelfAuditResult`. Checks that cannot run are recorded in
        ``checks_skipped`` with a reason — never omitted, because a report that lists five
        clean checks and says nothing about the sixth reads as six clean checks.
    """
    result = SelfAuditResult()

    for check_id, run in (
        ("F-1", lambda: checks_static.check_verifier_sensitivity(root)),
        ("F-2", lambda: checks_static.check_callpath_parity(root)),
        ("F-3", lambda: checks_static.check_canonical_resolution(root)),
        ("F-4", lambda: checks_report.check_counts_have_population(
            root, report_dir, rendered=rendered)),
    ):
        result.findings.extend(run())
        result.checks_run.append(check_id)

    result.advisory.extend(
        checks_report.check_counts_have_population(root, report_dir, historical=True)
    )

    if probes:
        result.findings.extend(checks_report.check_probe_intent(root, probes))
        result.findings.extend(checks_report.check_accepted_weaknesses(root, probes))
        result.checks_run.extend(("F-5", "F-6"))
    else:
        reason = (
            "no probe module was registered, so there are no probe outcomes to compare or "
            "accept — this is an unmeasured population, not a clean result"
        )
        result.checks_skipped["F-5"] = reason
        result.checks_skipped["F-6"] = reason

    return result


__all__ = ["CHECK_IDS", "CHECK_TITLES", "SelfAuditResult", "run_selfaudit"]
