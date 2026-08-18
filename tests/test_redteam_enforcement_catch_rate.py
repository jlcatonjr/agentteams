"""Tests for scripts/redteam_enforcement_catch_rate.py."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "redteam_enforcement_catch_rate",
    Path(__file__).resolve().parents[1] / "scripts" / "redteam_enforcement_catch_rate.py",
)
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)  # type: ignore[union-attr]


def _ledger(tmp_path, rows):
    p = tmp_path / "verifiers.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["module", "symbol", "kind", "sensitivity_test", "negative_control_test", "reason"]
        )
        w.writeheader()
        w.writerows(rows)
    return p


def test_live_ledger_reports_real_catch_rate():
    # Against the real ledger + ENFORCEMENT_MODULES: a valid rate in [0,1] and covered+uncovered
    # partition the enforcement set with no overlap.
    r = cr.analyze()
    assert 0.0 <= r["catch_rate"] <= 1.0
    assert r["enforcement_modules"] == len(r["covered"]) + len(r["uncovered"])
    assert not (set(r["covered"]) & set(r["uncovered"]))


def test_a_module_covered_only_if_both_tests_present(tmp_path):
    from agentteams import integrity
    mods = sorted(integrity.ENFORCEMENT_MODULES)
    # Cover the first module fully; give the second only a sensitivity test (must NOT count).
    rows = [
        {"module": mods[0], "symbol": "f", "kind": "verifier",
         "sensitivity_test": "t::a", "negative_control_test": "t::b", "reason": ""},
        {"module": mods[1], "symbol": "g", "kind": "verifier",
         "sensitivity_test": "t::c", "negative_control_test": "", "reason": ""},
    ]
    r = cr.analyze(_ledger(tmp_path, rows))
    assert mods[0] in r["covered"]
    assert mods[1] in r["uncovered"]  # missing negative control -> not covered


def test_render_names_the_gap(tmp_path):
    r = cr.analyze(_ledger(tmp_path, []))  # empty ledger -> everything uncovered
    out = cr.render(r)
    assert "UNCOVERED" in out and "not a gate" in out
    assert r["catch_rate"] == 0.0
