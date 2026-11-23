"""test_plan_steps_set.py — ``python -m agentteams.plan_steps --set``: one command to mark a step.

Sessions kept writing their own scripts to edit a steps CSV, and tables ended up with statuses
outside the vocabulary and with shifted columns. ``--set`` writes through ``update_step`` and
refuses a status the tools do not know.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import plan_steps as ps

_COLS = ["step", "agent", "action", "inputs", "outputs", "status", "notes", "depends_on"]


def _table(tmp_path: Path) -> Path:
    p = tmp_path / "x.steps.csv"
    ps.write_steps(p, [
        {"step": "1", "agent": "@a", "action": "write (a, b), then \"check\"", "inputs": "in", "outputs": "out",
         "status": "pending", "notes": "", "depends_on": ""},
        {"step": "2", "agent": "@b", "action": "review", "inputs": "out", "outputs": "notes",
         "status": "pending", "notes": "first", "depends_on": "1"},
    ], _COLS)
    return p


def test_set_status_and_note_leaves_every_other_cell_alone(tmp_path: Path) -> None:
    p = _table(tmp_path)
    before = ps.read_steps(p)
    row = ps.set_step(p, "1", status="done", note="commit abc, 3 files")
    assert row["status"] == "done" and row["notes"] == "commit abc, 3 files"
    after = ps.read_steps(p)
    assert after[1] == before[1]
    assert {k: v for k, v in after[0].items() if k not in ("status", "notes")} == \
           {k: v for k, v in before[0].items() if k not in ("status", "notes")}
    assert ps.validate(p) == ([], [])


def test_status_is_normalised_and_append_note_keeps_what_was_there(tmp_path: Path) -> None:
    p = _table(tmp_path)
    assert ps.set_step(p, "2", status=" In_Progress ")["status"] == "in_progress"
    assert ps.set_step(p, "2", append_note="second")["notes"] == "first; second"
    assert ps.set_step(p, "1", append_note="only")["notes"] == "only"


@pytest.mark.parametrize("status", ["completed", "in-progress", "DONE!", "todo", ""])
def test_a_status_outside_the_vocabulary_is_refused_and_nothing_is_written(tmp_path: Path, status: str) -> None:
    p = _table(tmp_path)
    before = p.read_bytes()
    with pytest.raises(ValueError):
        ps.set_step(p, "1", status=status)
    assert p.read_bytes() == before


def test_refusals_leave_the_file_untouched(tmp_path: Path) -> None:
    p = _table(tmp_path)
    before = p.read_bytes()
    with pytest.raises(KeyError):
        ps.set_step(p, "9", status="done")
    with pytest.raises(ValueError):
        ps.set_step(p, "1")
    with pytest.raises(ValueError):
        ps.set_step(p, "1", note="a", append_note="b")
    assert p.read_bytes() == before


def test_a_duplicated_step_id_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "dup.steps.csv"
    ps.write_steps(p, [{c: ("1" if c == "step" else "pending" if c == "status" else "") for c in _COLS}] * 2, _COLS)
    before = p.read_bytes()
    with pytest.raises(ValueError, match="names 2 rows"):
        ps.set_step(p, "1", status="done")
    assert p.read_bytes() == before


def test_a_table_without_the_column_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "legacy.steps.csv"
    p.write_text("step,description\n1,do it\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ps.set_step(p, "1", status="done")
    assert p.read_text(encoding="utf-8") == "step,description\n1,do it\n"


def test_cli_set(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _table(tmp_path)
    assert ps.main(["--set", str(p), "1", "--status", "done", "--note", "ok"]) == 0
    assert capsys.readouterr().out.strip() == f"OK {p}: step 1 status=done"
    assert ps.read_steps(p)[0]["notes"] == "ok"

    assert ps.main(["--set", str(p), "1", "--status", "completed"]) == 1
    assert "is not one of" in capsys.readouterr().out
    assert ps.main(["--set", str(p), "7", "--status", "done"]) == 1
    assert "no step '7'" in capsys.readouterr().out
    assert ps.main(["--set", str(p), "1"]) == 1
    assert ps.main(["--set", str(tmp_path / "none.csv"), "1", "--status", "done"]) == 1
    capsys.readouterr()


def test_cli_modes_do_not_mix(tmp_path: Path) -> None:
    p = _table(tmp_path)
    for argv in (["--check", str(p), "--status", "done"], ["--check", str(p), "--set", str(p), "1"], [],
                 ["--set", str(p), "1", "--note", "a", "--append-note", "b"]):
        with pytest.raises(SystemExit) as exc:
            ps.main(argv)
        assert exc.value.code == 2
    assert ps.main(["--check", str(p)]) == 0
