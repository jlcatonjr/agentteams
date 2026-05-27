"""Tests for agentteams.plan_steps_todo (Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import plan_steps_todo as pst

_HEADER = "phase_id,step_id,phase_name,step_title,description,deliverable,dependencies,priority,effort,notes,status\n"


def _write_csv(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def test_read_steps_parses_rows(tmp_path: Path):
    csv_path = tmp_path / "plan.steps.csv"
    _write_csv(csv_path, [
        '0,0.1,Setup,Initialize repo,desc,deliv,None,P0,S,note,done',
        '1,1.1,Build,Write parser,desc2,deliv2,0.1,P1,M,note2,pending',
    ])
    steps = pst.read_steps(csv_path)
    assert len(steps) == 2
    assert steps[0].step_id == "0.1"
    assert steps[0].status == "done"
    assert steps[1].priority == "P1"


def test_project_to_todos_maps_status(tmp_path: Path):
    csv_path = tmp_path / "plan.steps.csv"
    _write_csv(csv_path, [
        '0,0.1,Setup,Initialize repo,d,deliv,None,P0,S,n,done',
        '0,0.2,Setup,Add config,d,deliv,0.1,P0,XS,n,in_progress',
        '0,0.3,Setup,Write tests,d,deliv,0.1,P0,S,n,pending',
        '0,0.4,Setup,Block step,d,deliv,0.1,P0,S,n,blocked',
    ])
    todos = pst.project_to_todos(csv_path)
    assert todos[0]["status"] == "completed"  # done → completed
    assert todos[1]["status"] == "in_progress"
    assert todos[2]["status"] == "pending"
    assert todos[3]["status"] == "pending"    # blocked → pending
    assert todos[0]["content"].startswith("0.1:")


def test_update_status_preserves_other_columns(tmp_path: Path):
    csv_path = tmp_path / "plan.steps.csv"
    _write_csv(csv_path, [
        '0,0.1,Setup,Init,desc,deliv,None,P0,S,note,pending',
        '0,0.2,Setup,Config,desc,deliv,0.1,P0,XS,n,pending',
    ])
    pre = csv_path.read_text(encoding="utf-8")
    assert pst.update_status(csv_path, "0.2", "in_progress")
    post = csv_path.read_text(encoding="utf-8")
    # Header unchanged.
    assert post.split("\n")[0] == pre.split("\n")[0]
    steps = pst.read_steps(csv_path)
    assert steps[0].status == "pending"
    assert steps[1].status == "in_progress"
    # Non-status columns survive byte-for-byte.
    assert steps[1].description == "desc"
    assert steps[1].deliverable == "deliv"
    assert steps[1].dependencies == "0.1"


def test_update_status_rejects_unknown(tmp_path: Path):
    csv_path = tmp_path / "plan.steps.csv"
    _write_csv(csv_path, ['0,0.1,Setup,Init,d,deliv,None,P0,S,n,pending'])
    with pytest.raises(ValueError, match="unknown status"):
        pst.update_status(csv_path, "0.1", "frobnicated")


def test_update_status_missing_step_id_returns_false(tmp_path: Path):
    csv_path = tmp_path / "plan.steps.csv"
    _write_csv(csv_path, ['0,0.1,Setup,Init,d,deliv,None,P0,S,n,pending'])
    assert pst.update_status(csv_path, "99.99", "completed") is False


def test_detect_divergence_clean(tmp_path: Path):
    csv_path = tmp_path / "plan.steps.csv"
    _write_csv(csv_path, [
        '0,0.1,Setup,Init,d,deliv,None,P0,S,n,done',
        '0,0.2,Setup,Config,d,deliv,0.1,P0,XS,n,pending',
    ])
    todos = pst.project_to_todos(csv_path)
    d = pst.detect_divergence(csv_path, todos)
    assert d == {"missing_in_todo": [], "extra_in_todo": [], "status_mismatch": []}


def test_detect_divergence_flags_all_three(tmp_path: Path):
    csv_path = tmp_path / "plan.steps.csv"
    _write_csv(csv_path, [
        '0,0.1,Setup,Init,d,deliv,None,P0,S,n,pending',
        '0,0.2,Setup,Config,d,deliv,0.1,P0,XS,n,pending',
    ])
    todos = [
        # status mismatch on 0.1
        {"content": "0.1: Init", "activeForm": "Initializing", "status": "completed"},
        # missing in todo: 0.2 absent
        # extra in todo:
        {"content": "9.9: Bogus", "activeForm": "Doing bogus", "status": "pending"},
    ]
    d = pst.detect_divergence(csv_path, todos)
    assert d["missing_in_todo"] == ["0.2"]
    assert d["status_mismatch"] == ["0.1"]
    assert d["extra_in_todo"] == ["9.9: Bogus"]


def test_read_steps_validates_header(tmp_path: Path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        pst.read_steps(csv_path)


def test_render_skill_returns_skill_md():
    out = pst.render_skill()
    assert out.startswith("---")
    assert "name: todo-from-plan" in out
    assert "bridge: copilot-vscode-to-claude" in out


def test_read_real_repo_csv():
    """Smoke test against the actual build-team-steps.csv shipping in repo."""
    repo_root = Path(__file__).resolve().parents[1]
    csv_path = repo_root / "build-team-steps.csv"
    if not csv_path.exists():
        pytest.skip("build-team-steps.csv not present")
    steps = pst.read_steps(csv_path)
    assert len(steps) > 10
    todos = pst.project_to_todos(csv_path)
    assert len(todos) == len(steps)
    assert all("status" in t for t in todos)
