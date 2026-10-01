"""#21: plan steps CSV writer keeps commas quoted and preserves other cells."""

from __future__ import annotations

import pytest

from agentteams.plan_steps import read_steps, update_step, write_steps

_COLS = ["step", "agent", "action", "inputs", "outputs", "status", "notes"]


def test_write_and_update_roundtrip_with_commas(tmp_path):
    p = tmp_path / "x.steps.csv"
    write_steps(p, [{"step": "1", "agent": "@a", "action": "remove (acks,recipe,proxy)",
                     "inputs": "", "outputs": "", "status": "pending", "notes": 'he said "hi"'}], _COLS)
    update_step(p, "1", status="done", notes="literal paths, 17")
    (row,) = read_steps(p)
    assert row["action"] == "remove (acks,recipe,proxy)" and row["notes"] == "literal paths, 17"
    assert row["status"] == "done"


def test_unknown_column_and_step_refused(tmp_path):
    p = tmp_path / "x.steps.csv"
    write_steps(p, [{c: "1" if c == "step" else "" for c in _COLS}], _COLS)
    with pytest.raises(ValueError):
        update_step(p, "1", bogus="x")
    with pytest.raises(KeyError):
        update_step(p, "9", status="done")
