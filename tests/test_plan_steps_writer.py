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


def test_update_step_rewrites_one_row_and_keeps_every_other_byte(tmp_path):
    # CRLF endings, a blank line, a row wider than the header and a quoted cell all survive an update elsewhere.
    p = tmp_path / "x.steps.csv"
    before = b'step,status,notes\r\n1,pending,"a, b"\r\n\r\n2,pending,x,stray\r\n3,pending,\r\n'
    p.write_bytes(before)
    update_step(p, " 3 ", status="done", notes="ok")
    assert p.read_bytes() == before.replace(b"3,pending,\r\n", b"3,done,ok\r\n")
    assert [f.name for f in tmp_path.iterdir()] == ["x.steps.csv"]


def test_update_step_refuses_a_duplicate_id_and_a_wide_target_row(tmp_path):
    p = tmp_path / "x.steps.csv"
    text = "step,status\n1,pending\n1,pending\n2,pending,stray\n"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="names 2 rows"):
        update_step(p, "1", status="done")
    with pytest.raises(ValueError, match="more cells than the header"):
        update_step(p, "2", status="done")
    with pytest.raises(ValueError, match="not header columns"):
        update_step(p, "1", bogus="x")
    assert p.read_text(encoding="utf-8") == text


def test_update_step_sets_any_column_without_a_vocabulary(tmp_path):
    p = tmp_path / "x.steps.csv"
    write_steps(p, [{c: "1" if c == "step" else "" for c in _COLS}], _COLS)
    update_step(p, "1", status="completed", agent="@b")
    (row,) = read_steps(p)
    assert row["status"] == "completed" and row["agent"] == "@b"
    update_step(p, "1")  # nothing to set: no error, no change
    assert read_steps(p) == [row]


def test_write_steps_refuses_before_touching_the_file(tmp_path):
    # A cell holding a carriage return does not read back as given; the old file must survive the refusal.
    p = tmp_path / "x.steps.csv"
    p.write_text("step,status\n1,pending\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nothing was written"):
        write_steps(p, [{"step": "1", "status": "a\rb"}], ["step", "status"])
    with pytest.raises(ValueError, match="outside the header"):
        write_steps(p, [{"step": "1", "status": "x", "extra": "y"}], ["step", "status"])
    assert p.read_text(encoding="utf-8") == "step,status\n1,pending\n"
    assert [f.name for f in tmp_path.iterdir()] == ["x.steps.csv"]


def test_write_steps_keeps_crlf_and_a_symlink(tmp_path):
    real = tmp_path / "real.steps.csv"
    real.write_bytes(b"step,status\r\n1,pending\r\n")
    link = tmp_path / "link.steps.csv"
    link.symlink_to(real)
    write_steps(link, [{"step": "1", "status": "done"}, {"step": "2", "status": "pending"}], ["step", "status"])
    assert link.is_symlink()
    assert real.read_bytes() == b"step,status\r\n1,done\r\n2,pending\r\n"
    new = tmp_path / "new.steps.csv"
    write_steps(new, [{"step": "1", "status": "done"}], ["step", "status"])
    assert new.read_bytes() == b"step,status\n1,done\n"
