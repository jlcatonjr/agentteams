"""Tests for Coordinated Concurrency (Workflow 0B) in agentteams.parallel_plan.

Covers coordination_candidates() opt-in semantics, the R1 untagged fail-safe,
the depends_on contradiction case, the shared-mutable-state carve-out, the
non-overlapping skip, coordinate-column-tolerant CSV parsing, and the invariants
that to_json exposes coordination_candidates while compute_waves stays unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentteams import parallel_plan as pp

# Classic 7-column runtime header PLUS depends_on — deliberately WITHOUT a
# coordinate column, to exercise read_steps' tolerance (case f).
_HEADER_NO_COORD = "step,agent,action,inputs,outputs,status,notes,depends_on\n"


def _write_no_coord(path: Path, rows: list[str]) -> Path:
    path.write_text(_HEADER_NO_COORD + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# (a) same coordinate label + same write path, no depends_on -> ONE group
# ---------------------------------------------------------------------------

def test_a_same_label_overlap_yields_one_group():
    steps = [
        pp.PlanStep(step="A", agent="prod", action="edit doc",
                    inputs="src", outputs="templates/x.md", coordinate="docs-consistency"),
        pp.PlanStep(step="B", agent="prod", action="edit doc",
                    inputs="src", outputs="templates/x.md", coordinate="docs-consistency"),
    ]
    groups = pp.coordination_candidates(steps)
    assert len(groups) == 1
    g = groups[0]
    assert g.label == "docs-consistency"
    assert g.members == ["A", "B"]
    assert g.regions  # non-empty overlapping footprint tokens
    assert "templates/x.md" in g.regions
    assert g.warnings == []


# ---------------------------------------------------------------------------
# (b) same write path but NO coordinate tag -> [] (R1 fail-safe)
# ---------------------------------------------------------------------------

def test_b_untagged_overlap_is_never_promoted():
    steps = [
        pp.PlanStep(step="A", agent="prod", action="edit doc",
                    inputs="src", outputs="templates/x.md"),
        pp.PlanStep(step="B", agent="prod", action="edit doc",
                    inputs="src", outputs="templates/x.md"),
    ]
    assert pp.coordination_candidates(steps) == []


# ---------------------------------------------------------------------------
# (c) same label + mutual depends_on -> excluded, carries a warning
# ---------------------------------------------------------------------------

def test_c_same_label_with_mutual_depends_on_is_excluded_with_warning():
    steps = [
        pp.PlanStep(step="A", agent="prod", action="edit doc",
                    inputs="src", outputs="templates/x.md", coordinate="docs-consistency"),
        pp.PlanStep(step="B", agent="prod", action="edit doc",
                    inputs="src", outputs="templates/x.md", coordinate="docs-consistency",
                    depends_on="A"),
    ]
    groups = pp.coordination_candidates(steps)
    # The implementation emits the group carrying a warning, with empty regions.
    assert len(groups) == 1
    g = groups[0]
    assert g.label == "docs-consistency"
    assert g.members == ["A", "B"]
    assert g.regions == []
    assert g.warnings, "a contradiction warning must be attached"
    joined = " ".join(g.warnings).lower()
    assert "depends_on" in joined
    assert "b" in joined  # names the offending member


# ---------------------------------------------------------------------------
# (d) coordinate-tagged member touching shared mutable state -> excluded
# ---------------------------------------------------------------------------

def test_d_shared_mutable_state_member_excludes_group():
    steps = [
        pp.PlanStep(step="A", agent="prod", action="edit doc",
                    inputs="src", outputs="templates/x.md", coordinate="docs-consistency"),
        pp.PlanStep(step="B", agent="git", action="apply database migration",
                    inputs="src", outputs="templates/x.md", coordinate="docs-consistency"),
    ]
    # B touches shared state (git/database/migration) -> whole group excluded.
    assert steps[1].touches_shared_state() is True
    assert pp.coordination_candidates(steps) == []


# ---------------------------------------------------------------------------
# (e) coordinate-tagged steps with disjoint footprints -> no group
# ---------------------------------------------------------------------------

def test_e_non_overlapping_labelled_steps_yield_no_group():
    steps = [
        pp.PlanStep(step="A", agent="prod", action="edit doc",
                    inputs="in/x", outputs="templates/a.md", coordinate="docs-consistency"),
        pp.PlanStep(step="B", agent="prod", action="edit doc",
                    inputs="in/y", outputs="templates/b.md", coordinate="docs-consistency"),
    ]
    assert pp.coordination_candidates(steps) == []


# ---------------------------------------------------------------------------
# (f) read_steps tolerates a CSV with no coordinate column
# ---------------------------------------------------------------------------

def test_f_read_steps_without_coordinate_column(tmp_path: Path):
    p = _write_no_coord(tmp_path / "p.steps.csv", [
        "A,prod,write a,in/x,out/a,pending,,",
        "B,prod,write b,in/y,out/b,pending,,",
    ])
    steps = pp.read_steps(p)
    assert [s.step for s in steps] == ["A", "B"]
    assert all(s.coordinate == "" for s in steps)
    assert all(s.coordinate_label() == "" for s in steps)
    # No coordinate tags -> no coordination candidates.
    assert pp.coordination_candidates(steps) == []


# ---------------------------------------------------------------------------
# to_json exposes the coordination_candidates key
# ---------------------------------------------------------------------------

def test_to_json_includes_coordination_candidates_key():
    steps = [
        pp.PlanStep(step="A", agent="prod", action="edit doc",
                    inputs="src", outputs="templates/x.md", coordinate="docs-consistency"),
        pp.PlanStep(step="B", agent="prod", action="edit doc",
                    inputs="src", outputs="templates/x.md", coordinate="docs-consistency"),
    ]
    sched = pp.compute_waves(steps)
    groups = pp.coordination_candidates(steps)
    payload = json.loads(pp.to_json(sched, groups))
    assert "coordination_candidates" in payload
    assert len(payload["coordination_candidates"]) == 1
    cc = payload["coordination_candidates"][0]
    assert cc["label"] == "docs-consistency"
    assert cc["members"] == ["A", "B"]
    assert cc["regions"]

    # Key is present (empty list) even with no groups passed.
    payload_none = json.loads(pp.to_json(sched))
    assert payload_none["coordination_candidates"] == []


# ---------------------------------------------------------------------------
# compute_waves is UNCHANGED by the presence/absence of coordinate tags
# ---------------------------------------------------------------------------

def test_compute_waves_unaffected_by_coordinate_tags():
    disjoint = [
        pp.PlanStep(step="A", agent="prod", action="write a", inputs="in/x", outputs="out/a"),
        pp.PlanStep(step="B", agent="prod", action="write b", inputs="in/y", outputs="out/b"),
    ]
    tagged = [
        pp.PlanStep(step="A", agent="prod", action="write a", inputs="in/x", outputs="out/a",
                    coordinate="grp"),
        pp.PlanStep(step="B", agent="prod", action="write b", inputs="in/y", outputs="out/b",
                    coordinate="grp"),
    ]
    sched_untagged = pp.compute_waves(disjoint)
    sched_tagged = pp.compute_waves(tagged)
    assert sched_untagged.waves == sched_tagged.waves == [["A", "B"]]
    assert sched_untagged.max_parallelism == sched_tagged.max_parallelism == 2
    assert sched_untagged.reasons == sched_tagged.reasons
    assert sched_untagged.errors == sched_tagged.errors
    assert sched_untagged.warnings == sched_tagged.warnings
    # And tagging disjoint steps produces no coordination group either.
    assert pp.coordination_candidates(tagged) == []
