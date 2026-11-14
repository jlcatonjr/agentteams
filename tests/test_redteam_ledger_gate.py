"""test_redteam_ledger_gate.py — keep the red-team ledgers complete on every PR (#50 G4).

The weekly red-team audit (``redteam-audit.yml``) reported F-1 growing from 91 to 107 findings in one week: new
verifier-shaped functions landed without a row in ``references/redteam-verifiers.csv``, and each one is a verifier
whose sensitivity is unmeasured. A weekly issue cannot stop that; a failing test in the required ``test`` matrix can.

This runs the two source-reading self-audit checks, which need no probes and finish in seconds:

* **F-1** (:func:`check_verifier_sensitivity`): every verifier-shaped function (and non-Python enforcement module) has a
  ledger row naming a sensitivity test and a negative control that resolve, or a ``not-a-verifier`` reason; no row is
  stale.
* **F-3** (:func:`check_canonical_resolution`): no hand-rolled target, descriptor or VCS resolution without an
  exemption row and reason; no stale exemption.

F-5 (probe intent) needs the probe battery and an operator-accepted baseline, so it stays with the weekly audit.
"""
from __future__ import annotations

from pathlib import Path

from agentteams.redteam.checks_static import check_canonical_resolution, check_verifier_sensitivity

ROOT = Path(__file__).resolve().parent.parent


def _render(findings: list) -> str:
    return "\n".join(f"  {f.subject}: {f.detail}\n    remedy: {f.remedy}" for f in findings)


def test_every_verifier_has_a_ledger_row() -> None:
    """F-1 is clean: a new verifier needs a row in references/redteam-verifiers.csv in the same PR."""
    findings = check_verifier_sensitivity(ROOT)
    assert not findings, f"{len(findings)} F-1 finding(s):\n{_render(findings)}"


def test_no_unexempted_hand_rolled_resolution() -> None:
    """F-3 is clean: use the canonical resolver, or add an exemption row with a reason in the same PR."""
    findings = check_canonical_resolution(ROOT)
    assert not findings, f"{len(findings)} F-3 finding(s):\n{_render(findings)}"


def test_the_gate_is_sensitive_to_a_missing_row(tmp_path: Path) -> None:
    """Sensitivity: the same check reports a finding once a verifier's ledger row is removed."""
    import shutil

    for rel in ("agentteams", "references", "tests"):
        shutil.copytree(ROOT / rel, tmp_path / rel, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    ledger = tmp_path / "references" / "redteam-verifiers.csv"
    lines = ledger.read_text(encoding="utf-8").splitlines(keepends=True)
    victim = next(line for line in lines[1:] if line.startswith("agentteams/cli/security_gate.py,"))
    ledger.write_text("".join(line for line in lines if line != victim), encoding="utf-8")
    subjects = {f.subject for f in check_verifier_sensitivity(tmp_path)}
    module, symbol = victim.split(",")[:2]
    assert f"{module}::{symbol}" in subjects
