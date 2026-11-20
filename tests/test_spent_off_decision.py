"""``sandbox_relaxation_gate.spent_off_decision``: the public, read-only spent ``sandbox-wasm-off`` check (#574).

It returns ``<key_id>@<sig16>`` only for a governed workspace whose newest ``sandbox-wasm-off:<fw>`` row is consumed,
authorizing, Ed25519-signed and authentic, on an intact chain, with no unretracted HALT; any failure is a
``SandboxRelaxationError`` naming the check, with paths removed. It writes nothing. The private boolean the
stale-evidence excuse uses keeps its answers (``@security`` design conditions 1-10, 2026-11-20).
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from agentteams.cli import sandbox_relaxation_gate as G
from agentteams.cli.sandbox_relaxation_gate import SandboxRelaxationError, spent_off_decision
from tests._sandbox_gate_helpers import _COLUMNS, _KEY_ID, _baseline_row, _row, _team

FW = "claude"
ACTION = f"sandbox-wasm-off:{FW}"
FIELDS = [*_COLUMNS, "consumed"]


def _write(team: Path, *rows: dict[str, str]) -> Path:
    path = team / "references" / "security-decisions.log.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows([{c: "" for c in FIELDS} | _baseline_row(), *rows])
    return path


def _spent(**kw: str) -> dict[str, str]:
    consumed = kw.pop("consumed", "yes")
    return _row(ACTION, **kw) | {"consumed": consumed}


def _refused(team: Path, fragment: str, fw: str = FW) -> str:
    with pytest.raises(SandboxRelaxationError) as info:
        spent_off_decision(team, fw)
    assert fragment in str(info.value), str(info.value)
    return str(info.value)


def test_a_spent_signed_decision_returns_its_id(tmp_path: Path, stub_ed25519) -> None:
    team = _team(tmp_path)
    row = _spent()
    _write(team, row)
    assert spent_off_decision(team, FW) == f"{_KEY_ID}@{row['signature'][:16]}"
    assert G._spent_off_decision(team, ACTION) is True


def test_it_writes_nothing(tmp_path: Path, stub_ed25519) -> None:
    team = _team(tmp_path)
    log = _write(team, _spent())
    before = (log.read_bytes(), os.stat(log).st_mtime_ns, sorted(p.name for p in (team / "references").iterdir()))
    spent_off_decision(team, FW)
    with pytest.raises(SandboxRelaxationError):
        spent_off_decision(team, "codex")   # a refusal writes nothing either
    after = (log.read_bytes(), os.stat(log).st_mtime_ns, sorted(p.name for p in (team / "references").iterdir()))
    assert before == after


@pytest.mark.parametrize("row, fragment, private", [
    (lambda: _spent(consumed=""), "not marked consumed", False),
    (lambda: _spent(verdict="FAIL"), "does not authorize", False),
    (lambda: _spent(verdict="CONDITIONAL PASS") | {"conditions_verified": "pending"}, "CONDITIONAL PASS", False),
    (lambda: _spent() | {"signature": "stub" + "0" * 64}, "failed Ed25519 verification", False),
    (lambda: _spent(scope="sandbox-wasm-off:codex"), f"no decision row is scoped to {ACTION}", False),
], ids=["unconsumed", "fail-verdict", "cp-unverified", "bad-signature", "other-scope"])
def test_each_failed_check_is_a_refusal_and_the_private_answer_is_unchanged(tmp_path: Path, stub_ed25519, row,
                                                                          fragment: str, private: bool) -> None:
    team = _team(tmp_path)
    _write(team, row())
    _refused(team, fragment)
    assert G._spent_off_decision(team, ACTION) is private


def test_an_hmac_row_is_refused_publicly(tmp_path: Path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _write(team, _spent(scheme="hmac"))
    _refused(team, "REQUIRES ed25519")


def test_a_newer_unconsumed_row_hides_the_spent_one(tmp_path: Path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _write(team, _spent(), _spent(consumed="", stamp="2026-11-01"))
    _refused(team, "not marked consumed")


def test_an_unretracted_halt_refuses(tmp_path: Path, stub_ed25519) -> None:
    team = _team(tmp_path)
    halt = {c: "" for c in FIELDS} | {"timestamp": "2026-11-02", "requesting_agent": "security",
                                      "action_reviewed": "sandbox-wasm-off", "verdict": "HALT"}
    _write(team, _spent(), halt)
    _refused(team, "is HALT")
    assert G._spent_off_decision(team, ACTION) is False


def test_an_ungoverned_workspace_is_refused(tmp_path: Path, stub_ed25519) -> None:
    team = _team(tmp_path, governed=False)
    _write(team, _spent())
    _refused(team, "not governed")


def test_no_decisions_log_is_refused(tmp_path: Path) -> None:
    _refused(_team(tmp_path), "no decisions log")


@pytest.mark.parametrize("fw", ["model-substrate", "not-a-framework", "", "../claude"])
def test_only_a_rendered_framework_is_a_valid_qualifier(tmp_path: Path, fw: str) -> None:
    _refused(_team(tmp_path), "framework_id must be a framework", fw)


def test_a_refusal_reason_carries_no_path(tmp_path: Path) -> None:
    team = _team(tmp_path)
    (team / "references" / "security-decisions.log.csv").mkdir()   # reading it raises an OSError naming the path
    message = _refused(team, "<path>")
    assert str(tmp_path) not in message and os.sep + "references" + os.sep not in message


def test_it_is_public() -> None:
    assert "spent_off_decision" in G.__all__
