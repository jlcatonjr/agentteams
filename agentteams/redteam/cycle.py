"""cycle.py — the standing daily audit: phase order, artifacts, and the exit-code policy.

**What the daily job does, and what it deliberately does not.** It runs phase 1 (attack),
phase 2 (review), phase 6 (evaluate the red team), and emits a phase 3 *skeleton*. It does not
run phases 4, 5 or 7. An unattended job that writes remediation code is a larger risk than the
one it closes, and a plan that arrives pre-decided invites approval rather than review.

**Three distinguishable failures, because conflating them is the dangerous part.**

===== ============================================================ ==========================
Code  Meaning                                                      Reaction
===== ============================================================ ==========================
0     no exploit, no self-audit finding, live tree pristine        silent
1     a finding: a measured attack is live, or phase 6 found a     open/update the audit issue
      defect in the red team itself
2     **the harness is broken** — a control probe failed, a probe   open/update the *distinct*
      module would not import, a corpus claim no longer matches    harness-broken issue
      the scanner, or the run modified the live agent tree
===== ============================================================ ==========================

Code 2 outranks code 1. A battery whose controls fail is measuring a broken instrument, and
reporting that as "no exploits" is the worst outcome available here. Indeterminate is not a
pass.

**No ``except`` clauses.** A probe that raises propagates as a traceback and a non-zero
interpreter exit; the shell driver classifies a traceback death as harness-broken. Catching it
here to synthesise exit 2 would add the broad ``except`` that CH-24 ratchets against, and
would recreate the ``PROBE-ERROR`` row the battery's own comment warns about: it would let a
battery of broken probes finish and report all-clear.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.redteam import realcopy, report as report_mod
from agentteams.redteam.checks_report import build_probe_baseline
from agentteams.redteam.kev_correlation import load_kev_terms
from agentteams.redteam.registry import PROBE_BASELINE_REL
from agentteams.redteam.runner import Findings, run_attack_phase
from agentteams.redteam.selfaudit import SelfAuditResult, run_selfaudit

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_HARNESS_BROKEN = 2

#: Where daily artifacts land, relative to the repository root. Gitignored and ephemeral, per
#: `references/filing-conventions.md`: nothing new at the repo root, and nothing tracked that
#: would produce a commit every night.
REPORT_ROOT_REL = "tmp/redteam"  # gitignored by design — see references/filing-conventions.md


@dataclass
class CycleResult:
    """The outcome of one standing audit run."""

    exit_code: int
    findings: Findings
    selfaudit: SelfAuditResult
    artifacts: dict[str, str] = field(default_factory=dict)
    written: list[Path] = field(default_factory=list)
    live_tree_modifications: list[str] = field(default_factory=list)
    live_tree_verifiable: bool = True

    def summary_lines(self) -> list[str]:
        """Return a short human-readable summary for the console and the CI job log."""
        lines = [
            f"probe module      : {self.findings.probe_module or 'none registered'}",
            f"probes run        : {len(self.findings.probes)}",
            f"measured exploits : {len(self.findings.exploited)}",
            f"self-audit        : {len(self.selfaudit.findings)} finding(s), "
            f"{len(self.selfaudit.checks_run)} of 6 checks ran",
        ]
        if not self.live_tree_verifiable:
            lines.append(
                "live tree         : UNVERIFIABLE (no git) — reported as unknown, not clean"
            )
        elif self.live_tree_modifications:
            lines.append(
                f"live tree         : MODIFIED — {len(self.live_tree_modifications)} path(s)"
            )
        else:
            lines.append("live tree         : pristine")
        for count in self.findings.counts:
            lines.append(f"  {count.render()}")
        return lines


def report_dir_for(root: Path, date_stamp: str) -> Path:
    """Return the artifact directory for a given date.

    Args:
        root: Repository root.
        date_stamp: ``YYYY-MM-DD``.

    Returns:
        ``<root>/tmp/redteam/<date_stamp>`` — a gitignored, ephemeral location.
    """
    return root / REPORT_ROOT_REL / date_stamp


def run_cycle(
    root: Path,
    *,
    probe_module_path: str | None,
    report_dir: Path,
    generated_at: str,
    dry_run: bool = False,
) -> CycleResult:
    """Run the standing audit and return its result.

    Args:
        root: Repository (or generated-team) root.
        probe_module_path: Dotted path to the probe module, or ``None`` to run phase 6 only.
            There is no default; see :func:`~agentteams.redteam.runner.run_attack_phase`.
        report_dir: Where artifacts are written when ``dry_run`` is false.
        generated_at: ISO-8601 timestamp, supplied by the caller so a run is reproducible.
        dry_run: When true, everything is computed and **nothing is written**. The renderers
            have no filesystem access at all, so this is a property of the code rather than a
            flag each writer has to remember.

    Returns:
        The :class:`CycleResult`, including the exit code.
    """
    verifiable = realcopy.live_tree_is_verifiable(root)
    before = realcopy.live_tree_fingerprint(root)

    findings = run_attack_phase(
        root, probe_module_path=probe_module_path, generated_at=generated_at
    )
    discoveries = report_mod.render_discoveries(findings)
    selfaudit = run_selfaudit(
        root,
        probes=findings.probes,
        report_dir=report_dir,
        rendered={report_mod.DISCOVERIES_NAME: discoveries},
    )
    kev_terms = load_kev_terms(root)
    texts = report_mod.artifact_texts(findings, selfaudit, kev_terms)
    texts[report_mod.DISCOVERIES_NAME] = discoveries

    # The delta, taken after the probes have run and before the artifacts are written: the
    # artifacts land in the gitignored report root, outside SNAPSHOT_PATHS, so writing them cannot
    # register here. What would register is a probe that reached the live agent tree.
    modifications = (
        realcopy.live_tree_modifications(before, realcopy.live_tree_fingerprint(root))
        if verifiable else []
    )

    written: list[Path] = []
    if not dry_run:
        written = report_mod.write_artifacts(report_dir, texts)
        findings_path = report_dir / report_mod.FINDINGS_NAME
        findings_path.write_text(
            json.dumps(findings.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        written.append(findings_path)

    if findings.harness_is_broken or modifications or not verifiable:
        exit_code = EXIT_HARNESS_BROKEN
    elif findings.exploited or selfaudit.findings:
        exit_code = EXIT_FINDINGS
    else:
        exit_code = EXIT_CLEAN

    return CycleResult(
        exit_code=exit_code,
        findings=findings,
        selfaudit=selfaudit,
        artifacts=texts,
        written=sorted(written),
        live_tree_modifications=modifications,
        live_tree_verifiable=verifiable,
    )


def accept_probe_baseline(
    root: Path, *, probe_module_path: str, generated_at: str, dry_run: bool = False
) -> tuple[int, Path]:
    """Re-record the probe baseline from a fresh run. **Operator command only.**

    The daily job never calls this. If it did, every probe whose meaning drifted overnight
    would be silently re-baselined and F-5 would measure nothing — the check would clear its
    own flag, which is a verification that always passes wearing yet another hat. Accepting a
    changed probe outcome costs a reviewed diff, deliberately.

    Args:
        root: Repository root.
        probe_module_path: Dotted path to the probe module. Required — there is nothing to
            baseline without probes.
        generated_at: ISO-8601 timestamp for the run.
        dry_run: When true, computes the baseline and writes nothing.

    Returns:
        ``(probe_count, baseline_path)``.

    Raises:
        RuntimeError: If ``dry_run`` is set. A dry run that rewrote the baseline would be a
            dry run with a side effect, and this is the one side effect that silences a check.
    """
    if dry_run:
        raise RuntimeError(
            "--accept-probe-baseline rewrites the committed baseline and is refused under "
            "--dry-run; run it deliberately, review the diff, and commit it"
        )
    findings = run_attack_phase(
        root, probe_module_path=probe_module_path, generated_at=generated_at
    )
    path = root / PROBE_BASELINE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_probe_baseline(findings.probes)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return len(findings.probes), path


__all__ = [
    "EXIT_CLEAN",
    "EXIT_FINDINGS",
    "EXIT_HARNESS_BROKEN",
    "REPORT_ROOT_REL",
    "CycleResult",
    "accept_probe_baseline",
    "report_dir_for",
    "run_cycle",
]
