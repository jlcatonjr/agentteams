"""test_redteam_issue16_regression.py — the fixes that let the CI audit reach a clean verdict.

Issue #16: the standing red-team audit reported FINDINGS in CI for reasons that were artifacts of
the public-release scrub rather than real weaknesses:

* **Group B** — documented-limit acceptances lived only in the gitignored `accepted-weaknesses.csv`,
  invisible to CI. The fix adds a tracked, public companion ledger and unions the two, keeping the
  private "genuine weakness map" private. These tests pin the union and the disjoint-by-provenance
  guard (@security condition B-c2).
* **Group C** — E3 and E4 returned EXPLOITED in CI because they depended on gitignored *installed*
  artifacts absent there. E3 now measures the tracked template; E4/`integrity.verify` treat a
  legitimately-absent `INSTALLED_COPY` as benign while still catching a present-but-edited copy
  (the tamper tooth row 52 pinned) and a removed tracked template (E3's tooth). These tests pin all
  three states (@security condition C-c3).
"""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

from agentteams import integrity
from agentteams.redteam import registry
from agentteams.redteam.registry import (
    ACCEPTED_WEAKNESSES_REL,
    PUBLIC_ACCEPTANCES_REL,
)

REPO = Path(__file__).resolve().parents[1]


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Group B — public acceptances ledger + union loader
# ---------------------------------------------------------------------------

_HEADER = "pid,outcome,reason\n"


def test_public_acceptances_ledger_is_tracked() -> None:
    """The public ledger must ship — if it were gitignored, CI could not see the acceptances."""
    assert (REPO / PUBLIC_ACCEPTANCES_REL).exists()
    if (REPO / ".git").exists():
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch", PUBLIC_ACCEPTANCES_REL],
            cwd=REPO, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"{PUBLIC_ACCEPTANCES_REL} is not git-tracked"


def test_public_and_private_ledgers_are_disjoint() -> None:
    """B-c2: a PID lives in exactly one ledger; the loader never copies private → public."""
    def pids(rel: str) -> set[str]:
        p = REPO / rel
        if not p.exists():
            return set()
        with p.open(encoding="utf-8", newline="") as f:
            return {row["pid"] for row in csv.DictReader(f)}
    assert pids(PUBLIC_ACCEPTANCES_REL).isdisjoint(pids(ACCEPTED_WEAKNESSES_REL))


def test_load_accepted_weaknesses_unions_both_ledgers(tmp_path: Path) -> None:
    """F-6's source of truth is the union of the private and public ledgers."""
    _write(tmp_path, PUBLIC_ACCEPTANCES_REL, _HEADER + "B4,DOCUMENTED-LIMIT,a public documented limit reason well over forty chars\n")
    _write(tmp_path, ACCEPTED_WEAKNESSES_REL, _HEADER + "Z9,PARTIAL,a private sensitive acceptance reason well over forty chars\n")
    merged = registry.load_accepted_weaknesses(tmp_path)
    assert merged["B4"][0] == "DOCUMENTED-LIMIT"
    assert merged["Z9"][0] == "PARTIAL"


def test_private_ledger_wins_over_public_on_conflict(tmp_path: Path) -> None:
    """A public row can never shadow a sensitive private classification."""
    _write(tmp_path, PUBLIC_ACCEPTANCES_REL, _HEADER + "X1,DOCUMENTED-LIMIT,public says documented limit, forty-plus characters here\n")
    _write(tmp_path, ACCEPTED_WEAKNESSES_REL, _HEADER + "X1,EXPLOITED,private says exploited, this is the sensitive truth to keep\n")
    merged = registry.load_accepted_weaknesses(tmp_path)
    assert merged["X1"][0] == "EXPLOITED"


# ---------------------------------------------------------------------------
# Group C — INSTALLED_COPIES tolerant verify + E3 template repoint
# ---------------------------------------------------------------------------

def _manifest(root: Path, modules: dict[str, str]) -> None:
    _write(root, integrity.MANIFEST_REL_PATH,
           json.dumps({"version": 1, "note": "test", "modules": modules}, indent=2) + "\n")


def test_verify_treats_absent_installed_copy_as_benign(tmp_path: Path, monkeypatch) -> None:
    """C-c3(b): an absent INSTALLED_COPY is not a finding; an absent TRACKED module still is."""
    installed = ".claude/hooks/constitutional-gate.py"
    tracked = "agentteams/scan.py"
    monkeypatch.setattr(integrity, "ENFORCEMENT_MODULES", (installed, tracked))
    monkeypatch.setattr(integrity, "INSTALLED_COPIES", frozenset({installed}))
    _write(tmp_path, tracked, "x = 1\n")  # present + will match
    tracked_digest = integrity.compute_digests(tmp_path)[tracked]
    _manifest(tmp_path, {installed: "deadbeef" * 8, tracked: tracked_digest})

    findings = integrity.verify(tmp_path)
    assert findings == [], "absent installed copy must be benign"

    # And a TRACKED module going missing must still fire.
    (tmp_path / tracked).unlink()
    reasons = {f.rel_path: f.reason for f in integrity.verify(tmp_path)}
    assert reasons.get(tracked) == "missing"
    assert installed not in reasons  # still benign


def test_verify_flags_present_but_edited_installed_copy(tmp_path: Path, monkeypatch) -> None:
    """C-c3(a): the tamper tooth — a PRESENT installed copy whose bytes differ is caught."""
    installed = ".claude/hooks/constitutional-gate.py"
    monkeypatch.setattr(integrity, "ENFORCEMENT_MODULES", (installed,))
    monkeypatch.setattr(integrity, "INSTALLED_COPIES", frozenset({installed}))
    _write(tmp_path, installed, "the tampered installed gate\n")
    _manifest(tmp_path, {installed: "cafebabe" * 8})  # recorded digest != current

    reasons = {f.rel_path: f.reason for f in integrity.verify(tmp_path)}
    assert reasons.get(installed) == "modified", "a present, edited installed gate must be flagged"


def test_e4_detects_excludes_absent_installed_copy(tmp_path: Path, monkeypatch) -> None:
    """C-c1: E4's anti-vacuity `detects` must ignore an absent installed copy, not a tracked gap."""
    installed = ".claude/hooks/constitutional-gate.py"
    tracked = "agentteams/scan.py"
    monkeypatch.setattr(integrity, "ENFORCEMENT_MODULES", (installed, tracked))
    monkeypatch.setattr(integrity, "INSTALLED_COPIES", frozenset({installed}))
    _write(tmp_path, tracked, "x = 1\n")
    digests = integrity.compute_digests(tmp_path)  # installed -> "", tracked -> hash
    detects = bool(digests) and all(
        d for rel, d in digests.items() if not (rel in integrity.INSTALLED_COPIES and not d)
    )
    assert detects is True


def test_e3_measures_the_tracked_template_environment_independently() -> None:
    """C-c3 / E3: the probe reads the tracked template, so its verdict is the same everywhere.

    The gitignored installed copy may be absent (CI), but the tracked template ships in every
    checkout, so E3 is a stable DOCUMENTED-LIMIT — never the laptop-vs-CI split it used to be.
    """
    template = REPO / "agentteams" / "templates" / "universal" / "hooks" / "constitutional-gate.py"
    assert template.exists(), "the tracked hook template must ship in every checkout"

    from agentteams.redteam.registry import RESULTS
    import tests.constitutional_redteam_battery as battery

    RESULTS.clear()
    battery.e3_no_harness_level_clearance_hook()
    e3 = {p.pid: p for p in RESULTS}["E3"]
    assert e3.outcome == "DOCUMENTED-LIMIT"
    assert "templates/universal/hooks/constitutional-gate.py" in e3.evidence
    assert ".claude/hooks/constitutional-gate.py" not in e3.evidence
