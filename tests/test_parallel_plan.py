"""Tests for agentteams.parallel_plan — fail-safe parallel wave analysis."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import parallel_plan as pp

REPO_ROOT = Path(__file__).parent.parent
# Committed copy of the dogfood plan (the live plan lives under gitignored tmp/).
DOGFOOD_CSV = REPO_ROOT / "tests" / "fixtures" / "parallel_plan_dogfood.steps.csv"

_HEADER = "step,agent,action,inputs,outputs,status,notes,depends_on\n"


def _write(path: Path, rows: list[str]) -> Path:
    path.write_text(_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def test_read_steps_is_tolerant_of_missing_columns(tmp_path: Path):
    p = tmp_path / "p.steps.csv"
    p.write_text("step,action\nS1,do thing\n,blank-skipped\n", encoding="utf-8")
    steps = pp.read_steps(p)
    assert [s.step for s in steps] == ["S1"]  # blank-step row skipped
    assert steps[0].depends_on == ""


def test_read_steps_requires_step_column(tmp_path: Path):
    p = tmp_path / "p.steps.csv"
    p.write_text("agent,action\nx,y\n", encoding="utf-8")
    with pytest.raises(ValueError):
        pp.read_steps(p)


# ---------------------------------------------------------------------------
# Wave computation
# ---------------------------------------------------------------------------

def test_independent_steps_share_a_wave(tmp_path: Path):
    p = _write(tmp_path / "p.steps.csv", [
        "A,prod,write a,in/x,out/a,pending,,",
        "B,prod,write b,in/y,out/b,pending,,",
    ])
    sched = pp.analyze_plan(p)
    assert sched.errors == []
    assert sched.waves == [["A", "B"]]
    assert sched.max_parallelism == 2


def test_declared_dependency_orders_into_separate_waves(tmp_path: Path):
    p = _write(tmp_path / "p.steps.csv", [
        "A,prod,write a,in/x,out/a,pending,,",
        "B,prod,consume,out/z,out/b,pending,,A",
    ])
    sched = pp.analyze_plan(p)
    assert sched.waves == [["A"], ["B"]]


def test_cycle_is_blocking_error(tmp_path: Path):
    p = _write(tmp_path / "p.steps.csv", [
        "A,prod,a,i,out/a,pending,,B",
        "B,prod,b,i,out/b,pending,,A",
    ])
    sched = pp.analyze_plan(p)
    assert sched.waves == []
    assert sched.errors and "cycle" in sched.errors[0].lower()


def test_undeclared_read_after_write_is_inferred_and_serialized(tmp_path: Path):
    # B reads what A writes but does NOT declare depends_on — must still be ordered.
    p = _write(tmp_path / "p.steps.csv", [
        "A,prod,write shared,in/x,shared/file.txt,pending,,",
        "B,prod,read shared,shared/file.txt,out/b,pending,,",
    ])
    sched = pp.analyze_plan(p)
    assert sched.waves == [["A"], ["B"]]  # not the same wave
    assert any("read-after-write" in w for w in sched.warnings)


def test_directory_file_containment_conflicts(tmp_path: Path):
    # A writes a directory; B reads a file under it -> containment overlap -> ordered.
    p = _write(tmp_path / "p.steps.csv", [
        "A,prod,emit dir,src,examples/x/expected,pending,,",
        "B,prod,read file,examples/x/expected/orchestrator.agent.md,out/b,pending,,",
    ])
    sched = pp.analyze_plan(p)
    assert sched.waves == [["A"], ["B"]]


def test_shared_state_step_is_forced_singleton(tmp_path: Path):
    p = _write(tmp_path / "p.steps.csv", [
        "A,prod,write a,in/x,out/a,pending,,",
        "B,git,apply database migration,in/y,out/b,pending,,",
    ])
    sched = pp.analyze_plan(p)
    # A groups alone; B is isolated for shared-state contact.
    assert ["B"] in sched.waves
    assert "B" in sched.reasons
    assert "shared" in sched.reasons["B"].lower()


def test_empty_footprint_step_is_forced_singleton(tmp_path: Path):
    p = _write(tmp_path / "p.steps.csv", [
        "A,prod,write a,in/x,out/a,pending,,",
        "B,prod,think hard,,,pending,no footprint,",
    ])
    sched = pp.analyze_plan(p)
    assert ["B"] in sched.waves
    assert "B" in sched.reasons


def test_dogfood_csv_produces_expected_waves():
    assert DOGFOOD_CSV.exists(), "dogfood steps CSV must exist"
    sched = pp.analyze_plan(DOGFOOD_CSV)
    assert sched.errors == []
    assert len(sched.waves) == 4
    assert set(sched.waves[0]) == {"S1", "S2", "S3", "S4", "S5", "S6"}
    assert set(sched.waves[1]) == {"S7", "S8", "S9", "S10"}
    assert set(sched.waves[2]) == {"S11", "S12"}
    assert sched.waves[3] == ["S13"]
    assert sched.max_parallelism == 6


# ---------------------------------------------------------------------------
# Cross-plan independence
# ---------------------------------------------------------------------------

def test_cross_plan_disjoint_plans_are_separate_groups(tmp_path: Path):
    a = _write(tmp_path / "a.steps.csv", ["A1,p,act,in/a,out/a,pending,,"])
    b = _write(tmp_path / "b.steps.csv", ["B1,p,act,in/b,out/b,pending,,"])
    res = pp.independent_plans([a, b])
    assert len(res["any_order_groups"]) == 2  # mutually non-blocking
    assert res["undetermined"] == []


def test_cross_plan_overlapping_plans_are_one_group(tmp_path: Path):
    a = _write(tmp_path / "a.steps.csv", ["A1,p,act,in/a,shared/out,pending,,"])
    b = _write(tmp_path / "b.steps.csv", ["B1,p,act,shared/out,out/b,pending,,"])
    res = pp.independent_plans([a, b])
    assert len(res["any_order_groups"]) == 1  # entangled (read-after-write across plans)


def test_cross_plan_undetermined_when_no_write_footprint(tmp_path: Path):
    a = _write(tmp_path / "a.steps.csv", ["A1,p,act,,,pending,,"])
    res = pp.independent_plans([a])
    assert res["undetermined"] == [str(a)]


# ---------------------------------------------------------------------------
# Rendering / skill
# ---------------------------------------------------------------------------

def test_to_json_round_trips(tmp_path: Path):
    p = _write(tmp_path / "p.steps.csv", ["A,p,a,i,out/a,pending,,"])
    payload = json.loads(pp.to_json(pp.analyze_plan(p)))
    assert payload["waves"] == [["A"]]
    assert payload["max_parallelism"] == 1


def test_render_skill_has_frontmatter():
    skill = pp.render_skill()
    assert skill.startswith("---")
    assert "name: parallelize-plan" in skill
    assert "parallel_plan" in skill


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agentteams.parallel_plan", *args],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )


def test_cli_runs_and_reports_waves(tmp_path: Path):
    p = _write(tmp_path / "p.steps.csv", [
        "A,p,a,in/x,out/a,pending,,",
        "B,p,b,in/y,out/b,pending,,",
    ])
    cp = _run_cli(str(p))
    assert cp.returncode == 0, cp.stderr
    assert "Wave" in cp.stdout


def test_cli_json_mode(tmp_path: Path):
    p = _write(tmp_path / "p.steps.csv", ["A,p,a,i,out/a,pending,,"])
    cp = _run_cli(str(p), "--json")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert isinstance(data, list)


def test_cli_exits_nonzero_on_cycle(tmp_path: Path):
    p = _write(tmp_path / "p.steps.csv", [
        "A,p,a,i,out/a,pending,,B",
        "B,p,b,i,out/b,pending,,A",
    ])
    cp = _run_cli(str(p))
    assert cp.returncode == 1
