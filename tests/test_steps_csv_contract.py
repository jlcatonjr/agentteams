"""The Rule 9 steps-CSV contract is checked; done steps are not scheduled; todo-from-plan reads Rule 9 (IM item 09).

G-05-1  nothing validated the columns or the status vocabulary; `in_progress`/unknown rows vanished from Part B,
        and a CSV without `step` crashed parallel_plan with a traceback.
G-05-4  the todo-from-plan skill (plan_steps_todo) rejected every Rule 9 CSV.
G-05-5  parallel_plan scheduled rows that were already `done`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import plan_steps, plan_steps_todo, session_scan
from agentteams.parallel_plan import main as parallel_main

HEADER = "step,agent,action,inputs,outputs,status,notes,depends_on"


def _csv(path: Path, *rows: str, header: str = HEADER) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- G-05-1


def test_validate_reports_warnings_and_the_one_hard_error(tmp_path):
    good = _csv(tmp_path / "ok.steps.csv", "1,a,write,src/a.md,out/a.md,pending,,")
    assert plan_steps.validate(good) == ([], [])
    bad = _csv(tmp_path / "bad.steps.csv",
               "1,a,x,,,todo,,", "2,a,y,src/b.md,out/b.md,in_progress,,9", "2,a,z,src/c.md,out/c.md,done,,",
               header="step,agent,action,inputs,outputs,status,notes,depends_on")
    errors, warns = plan_steps.validate(bad)
    assert errors == []                                                    # warn-first
    text = " ".join(warns)
    assert "status 'todo'" in text and "no inputs/outputs" in text
    assert "depends on unknown step '9'" in text and "duplicate step id '2'" in text
    missing = _csv(tmp_path / "missing.steps.csv", "1,write", header="step,action")
    assert any("missing required column 'agent'" in w for w in plan_steps.validate(missing)[1])
    no_step = _csv(tmp_path / "nostep.steps.csv", "x,y", header="name,action")
    assert plan_steps.validate(no_step)[0]                                 # the one unusable shape


def test_check_cli_exit_codes(tmp_path, capsys):
    good = _csv(tmp_path / "ok.steps.csv", "1,a,write,src/a.md,out/a.md,pending,,")
    warn = _csv(tmp_path / "warn.steps.csv", "1,a,write,src/a.md,out/a.md,todo,,")
    no_step = _csv(tmp_path / "nostep.steps.csv", "x,y", header="name,action")
    assert plan_steps.main(["--check", str(good)]) == 0
    assert plan_steps.main(["--check", str(warn)]) == 0 and "WARNING" in capsys.readouterr().out
    assert plan_steps.main(["--check", str(no_step)]) == 1


def test_parallel_plan_reports_a_missing_step_column_instead_of_a_traceback(tmp_path, capsys):
    no_step = _csv(tmp_path / "nostep.steps.csv", "x,y", header="name,action")
    assert parallel_main([str(no_step)]) == 1
    assert "no 'step' column" in capsys.readouterr().err


def test_session_scan_keeps_in_progress_and_unknown_rows_open(tmp_path):
    plans = tmp_path / "tmp" / "by-week" / "2026-W46"
    plans.mkdir(parents=True)
    _csv(plans / "p.steps.csv", "1,a,doing,,,in_progress,,", "2,a,huh,,,todo,,", "3,a,fin,,,done,,",
         "4,a,old,,,completed,,", "5,a,wait,,,pending,,")
    issues = session_scan._scan_pending_blocked_steps(tmp_path)
    details = " ".join(i.detail for i in issues)
    assert "doing (status=in_progress)" in details
    assert "huh (status=todo; not a Rule 9 status)" in details
    assert "fin" not in details and "old" not in details and "wait (status=pending)" in details


def test_no_existing_agentteams_steps_csv_is_an_error():
    """IM: warn-first means agentteams' own plan CSVs produce warnings only."""
    tmp = Path(__file__).resolve().parents[1] / "tmp"
    paths = [p for p in tmp.rglob("*.steps.csv") if ".git" not in p.parts] if tmp.is_dir() else []
    if not paths:
        pytest.skip("no local tmp/ plans in this checkout (tmp/ is gitignored)")
    errored = [str(p) for p in paths if plan_steps.validate(p)[0]]
    assert not errored, errored[:5]


# --------------------------------------------------------------------------- G-05-4


def test_todo_from_plan_reads_a_rule_9_csv(tmp_path):
    path = _csv(tmp_path / "p.steps.csv", "1,a,Write the module,src/a.md,out/a.md,in_progress,n,",
                "2,b,Review,out/a.md,out/r.md,done,,1")
    todos = plan_steps_todo.project_to_todos(path)
    assert [t["status"] for t in todos] == ["in_progress", "completed"]
    assert todos[0]["content"] == "1: Write the module"
    assert plan_steps_todo.update_status(path, "1", "done") is True
    assert "1,a,Write the module,src/a.md,out/a.md,done" in path.read_text()


def test_todo_from_plan_still_reads_the_11_column_form(tmp_path):
    cols = "phase_id,step_id,phase_name,step_title,description,deliverable,dependencies,priority,effort,notes,status"
    path = _csv(tmp_path / "p.csv", "P1,S1,Ph,Do it,d,x,,high,s,,pending", header=cols)
    assert plan_steps_todo.project_to_todos(path)[0]["content"] == "S1: Do it"


# --------------------------------------------------------------------------- G-05-5


_PLAN = (
    "1,a,first,src/a.md,out/a.md,done,,",
    "2,a,second,out/a.md,out/b.md,pending,,1",
    "3,a,third,src/c.md,out/c.md,blocked,,",
    "4,a,fourth,out/c.md,out/d.md,pending,,3",
    "5,a,fifth,src/e.md,out/e.md,DONE-ish,,",
)


def test_done_rows_are_not_scheduled_and_satisfy_their_dependents(tmp_path, capsys):
    path = _csv(tmp_path / "p.steps.csv", *_PLAN)
    assert parallel_main([str(path), "--json"]) == 0
    sched = json.loads(capsys.readouterr().out)[0]["schedule"]
    scheduled = [s for wave in sched["waves"] for s in wave]
    assert "1" not in scheduled and "2" in scheduled                       # done dropped, its dependent runs
    assert "3" not in scheduled and "4" not in scheduled                   # blocked, and its dependent waits
    warns = " ".join(sched["warnings"])
    assert "step '4' waits on blocked step '3'" in warns
    assert "status 'done-ish'" in warns and "5" in scheduled               # unknown status reported, kept


def test_all_keeps_the_old_behaviour(tmp_path, capsys):
    path = _csv(tmp_path / "p.steps.csv", *_PLAN)
    assert parallel_main([str(path), "--json", "--all"]) == 0
    scheduled = [s for w in json.loads(capsys.readouterr().out)[0]["schedule"]["waves"] for s in w]
    assert {"1", "3", "4"} <= set(scheduled)


def test_runnable_propagates_waiting_transitively():
    from agentteams.parallel_plan import PlanStep

    steps = [PlanStep(step="a", status="blocked"), PlanStep(step="b", status="pending", depends_on="a"),
             PlanStep(step="c", status="pending", depends_on="b"), PlanStep(step="d", status="pending")]
    remaining, waiting, _ = plan_steps.runnable(steps)
    assert [s.step for s in remaining] == ["d"] and waiting == {"a": "a", "b": "a", "c": "a"}


# --------------------------------------------------------------------------- Workflow 11 Part A


def test_final_check_part_a_runs_the_validator():
    orch = (Path(__file__).resolve().parents[1] / "agentteams/templates/universal/orchestrator.template.md").read_text()
    part_a = orch[orch.index("#### Part A — Within-Plan Issues"):orch.index("#### Part B")]
    assert "python -m agentteams.plan_steps --check" in part_a
    assert "`in_progress`" in part_a and "`pending` or `blocked`" not in part_a
