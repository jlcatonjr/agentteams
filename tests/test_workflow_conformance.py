"""Workflow-conformance test, Phase A: spec, coverage (AC3), normaliser, scorer, controls (AC2), day markers.

Everything here runs on recorded or hand-built traces: no network, no model, no spend.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from agentteams import eval_suite
from agentteams.behavioral_drift import BEHAVIOR_BROKEN_CHAIN, detect_behavioral_drift
from agentteams.cli.artifacts import _load_schema_bytes, _schema_path, _validate_against_schema
from agentteams.redteam import stage_markers, workflow_score, workflow_spec, workflow_trace

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / workflow_score.FIXTURES_REL
TEMPLATE = workflow_spec.load_template(ROOT)
CORE = {"orchestrator", "primary-producer", "adversarial", "quality-auditor", "conflict-auditor",
        "technical-validator", "security", "cleanup", "work-summarizer", "auth-expert"}


def _agents(spec: dict) -> list[tuple[str, str]]:
    return [(r["step"], r["agent"]) for r in spec["required"]]


def _wf(wid: str):
    return next(w for w in workflow_spec.parse_workflows(TEMPLATE) if w.wid == wid)


# --- AC3: coverage derived from the template ---------------------------------------------------------------

def test_every_workflow_heading_has_exactly_one_coverage_class() -> None:
    """AC3: the heading set is read from the template, and each heading has a class (with a reason if not live)."""
    ids = workflow_spec.workflow_headings(TEMPLATE)
    assert ids, "no Workflow headings parsed"
    assert len(ids) == len(set(ids))
    assert workflow_spec.check_coverage_is_complete(TEMPLATE) == []
    assert set(ids) == set(workflow_spec.COVERAGE)


def test_coverage_check_flags_a_new_or_removed_heading() -> None:
    """Sensitivity: a heading the table does not know, or a class whose heading is gone, is reported."""
    added = TEMPLATE + "\n### Workflow 99: Something New\n\n1. Invoke `@adversarial` → x\n"
    assert "Workflow 99: no coverage class" in workflow_spec.check_coverage_is_complete(added)
    removed = TEMPLATE.replace("### Workflow 14:", "### Retired 14:")
    assert "Workflow 14: classified but not in the template" in workflow_spec.check_coverage_is_complete(removed)


def test_replay_only_classes_name_why_live_is_unsafe() -> None:
    """Only workflows a fresh tree cannot run safely are replay-only, each with its reason."""
    replay = {w: r for w, (c, r) in workflow_spec.COVERAGE.items() if c == workflow_spec.REPLAY_ONLY}
    assert set(replay) == {"9", "14"}
    assert all(r.startswith("unsafe:") for r in replay.values())


# --- item 1: spec derivation ----------------------------------------------------------------------------------

def test_workflow_7_requires_security_before_cleanup() -> None:
    spec = workflow_spec.derive_workflow_spec(_wf("7"), CORE)
    assert _agents(spec) == [("1", "technical-validator"), ("2", "adversarial"), ("3", "security"), ("4", "cleanup")]


def test_team_conditional_steps_follow_the_roster() -> None:
    """*(If `@x` in team)* steps are required with x in the roster and dropped without it."""
    without = workflow_spec.derive_workflow_spec(_wf("1"), CORE)
    with_cr = workflow_spec.derive_workflow_spec(_wf("1"), CORE | {"cohesion-repairer"})
    assert ("6", "cohesion-repairer") not in _agents(without)
    assert ("6", "cohesion-repairer") in _agents(with_cr)
    assert _agents(without)[-1] == ("8", "conflict-auditor")
    assert workflow_spec.derive_workflow_spec(_wf("10C"), CORE)["required"] == []
    seeded = workflow_spec.derive_workflow_spec(_wf("10C"), CORE | {"post-production-auditor"})
    assert _agents(seeded)[:2] == [("1", "post-production-auditor"), ("2", "adversarial")]


def test_runtime_conditional_steps_are_optional_not_required() -> None:
    """'If ...', '**Exception:**' and '*Only when*' steps never become required invocations."""
    wf3 = workflow_spec.derive_workflow_spec(_wf("3"), CORE)
    assert "primary-producer" in wf3["optional"]
    assert "primary-producer" not in {a for _, a in _agents(wf3)}
    wf11 = workflow_spec.derive_workflow_spec(_wf("11"), CORE | {"conflict-resolution"})
    assert "conflict-resolution" in wf11["optional"] and "work-summarizer" in wf11["optional"]


def test_workflow_suite_validates_against_the_eval_suite_schema() -> None:
    """build_workflow_suite emits the shared envelope; the schema accepts the new category and kinds."""
    suite = eval_suite.build_workflow_suite({"agent_slug_list": sorted(CORE), "project_name": "demo"}, TEMPLATE)
    schema = _load_schema_bytes(_schema_path("eval-suite.schema.json"), RuntimeError, "eval suite")
    _validate_against_schema(suite, schema, error_cls=RuntimeError, label="eval suite")
    kinds = {s["predicate"]["kind"] for s in suite["scenarios"]}
    assert kinds == {"workflow-sequence", "rule-order"}
    assert {s["predicate"]["rule"] for s in suite["scenarios"] if s["predicate"]["kind"] == "rule-order"} == set(
        workflow_spec.RULES)
    text = json.dumps(suite)
    assert not any(tok in text for tok in eval_suite.FRAMEWORK_COUPLED_TOKENS)


def test_emitted_eval_suite_is_unchanged() -> None:
    """Phase A keeps workflow scenarios out of build_eval_suite (the structural adapters would fail them)."""
    suite = eval_suite.build_eval_suite({"agent_slug_list": sorted(CORE), "components": []})
    assert all(s["category"] != "workflow" for s in suite["scenarios"])


# --- item 3: normaliser ---------------------------------------------------------------------------------------

def _claude(*items: dict) -> list[str]:
    return [json.dumps({"type": "assistant", "message": {"content": [it]}}) for it in items]


def test_agent_slug_normalises_host_names() -> None:
    assert workflow_trace.agent_slug("Security — AgentTeamsModule") == "security"
    assert workflow_trace.agent_slug("Code Hygiene — X") == "code-hygiene"
    assert workflow_trace.agent_slug("code_hygiene") == "code-hygiene"


def test_claude_stream_events_and_gate_denial() -> None:
    lines = _claude(
        {"type": "text", "text": "Workflow 7: Cleanup"},
        {"type": "tool_use", "id": "t1", "name": "Task", "input": {"subagent_type": "Security — Demo"}},
        {"type": "tool_use", "id": "t2", "name": "Write", "input": {"file_path": "/w/tmp/by-week/2026-W47/p.plan.md"}},
        {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "git reset --hard HEAD"}},
        {"type": "tool_use", "id": "t4", "name": "Edit", "input": {"file_path": "/elsewhere/x.md"}},
        {"type": "tool_use", "id": "t5", "name": "Bash", "input": {"command": "ls -la"}},
    ) + ["not json", ""]
    gate = [json.dumps({"tool_use_id": "t3", "decision": "deny"}), json.dumps({"tool_use_id": "t1", "decision": "pass"})]
    trace = workflow_trace.normalise("claude", lines, work_tree="/w", session_slug="s", gate_lines=gate)
    kinds = [(e["kind"], e.get("agent") or e.get("path") or e.get("workflow") or e.get("detail")) for e in trace["events"]]
    assert kinds == [("workflow", "7"), ("invoke", "security"), ("write", "tmp/by-week/2026-W47/p.plan.md"),
                     ("destructive", "git reset --hard"), ("write", "/elsewhere/x.md")]
    by_id = {e.get("tool_use_id"): e for e in trace["events"]}
    assert by_id["t3"]["denied"] and not by_id["t1"]["denied"]
    assert by_id["t2"]["plan_artifact"] == "plan" and by_id["t4"]["cross_repo"]
    assert [e["seq"] for e in trace["events"]] == [1, 2, 3, 4, 5]


def test_trajectory_is_one_chain_for_behavioral_drift() -> None:
    """Round-trip edges keep detect_behavioral_drift's contiguity check clean; denied invocations add no edge."""
    lines = _claude(*[{"type": "tool_use", "id": f"t{i}", "name": "Task", "input": {"subagent_type": a}}
                      for i, a in enumerate(["adversarial", "security", "cleanup"])])
    gate = [json.dumps({"tool_use_id": "t2", "decision": "deny"})]
    trace = workflow_trace.normalise("claude", lines, work_tree="/w", session_slug="s", gate_lines=gate)
    assert len(trace["handoff_edges"]) == 4
    codes = {f.code for f in detect_behavioral_drift(trace, {"scenarios": []})}
    assert BEHAVIOR_BROKEN_CHAIN not in codes


def test_unknown_host_is_refused() -> None:
    with pytest.raises(ValueError):
        workflow_trace.normalise("other", [], work_tree="/w", session_slug="s")


# --- item 4: scorer -------------------------------------------------------------------------------------------

def _score(events: list[dict], workflow: str = "7", roster: set[str] = CORE) -> dict:
    for i, ev in enumerate(events, 1):
        ev.setdefault("denied", False)
        ev["seq"] = i
    scenarios = workflow_spec.build_workflow_scenarios(TEMPLATE, {"agent_slug_list": sorted(roster)})
    return workflow_score.check_workflow_conformance({"events": events}, scenarios, workflow)


def _inv(agent: str) -> dict:
    return {"kind": "invoke", "agent": agent}


def test_scorer_accepts_a_conforming_run_with_extra_invocations() -> None:
    """Negative control: required order present, extras allowed, no rule triggered -> conforming."""
    res = _score([_inv("technical-validator"), _inv("navigator"), _inv("adversarial"), _inv("security"),
                  {"kind": "destructive", "detail": "rm"}, _inv("cleanup")])
    assert res["conforming"] and res["first_deviation"] is None
    assert res["rules"]["c5-security-first"]["verdict"] == "pass"


def test_scorer_reports_the_first_deviating_step() -> None:
    """Sensitivity: a missing required invocation is reported at its template step."""
    res = _score([_inv("technical-validator"), _inv("security"), _inv("cleanup")])
    assert not res["conforming"] and res["first_deviation"] == {"step": "2"}
    assert res["sequence"]["missing_agent"] == "adversarial"


def test_scorer_wildcard_expert_and_unknown_workflow() -> None:
    res = _score([_inv("auth-expert"), _inv("adversarial"), _inv("primary-producer"), _inv("auth-expert"),
                  _inv("quality-auditor"), _inv("conflict-auditor")], workflow="1")
    assert res["conforming"]
    with pytest.raises(KeyError):
        _score([], workflow="6")  # no invocations, so no sequence scenario


# --- item 5 / AC2: controls -----------------------------------------------------------------------------------

def test_controls_behave_on_the_recorded_fixtures() -> None:
    """AC2 negative control for the controls: every planted defect is caught at its step, every clean trace passes."""
    res = workflow_score.verify_conformance_controls(TEMPLATE, FIXTURES)
    assert not res.void, res.problems
    assert {c["control"] for c in res.cases} == {"positive", "negative"}
    names = {c["name"] for c in res.cases}
    assert "pos-wf10c-seeded-skips-adversarial" in names  # a newly-live workflow's seed is exercised
    assert any(c.get("host") == "goose" for c in json.loads((FIXTURES / "controls.json").read_text())["cases"])


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "fx"
    shutil.copytree(FIXTURES, dst)
    return dst


def test_controls_void_when_a_planted_defect_goes_unflagged(tmp_path: Path) -> None:
    """Sensitivity: if a positive control's expected step is wrong (the scorer 'misses' it), the run is VOID."""
    fx = _copy(tmp_path)
    data = json.loads((fx / "controls.json").read_text())
    pos = next(c for c in data["cases"] if c["control"] == "positive" and "step" in c.get("expect", {}))
    pos["expect"] = {"step": "999"}
    (fx / "controls.json").write_text(json.dumps(data))
    res = workflow_score.verify_conformance_controls(TEMPLATE, fx)
    assert res.void and any(pos["name"] in p for p in res.problems)


def test_controls_void_when_a_negative_control_is_flagged(tmp_path: Path) -> None:
    """Sensitivity: a 'clean' trace that is in fact non-conforming makes the run VOID."""
    fx = _copy(tmp_path)
    data = json.loads((fx / "controls.json").read_text())
    neg = next(c for c in data["cases"] if c["control"] == "negative")
    neg["workflow"] = "1"  # the Workflow 7 trace does not satisfy Workflow 1
    (fx / "controls.json").write_text(json.dumps(data))
    assert workflow_score.verify_conformance_controls(TEMPLATE, fx).void


def test_controls_void_without_both_kinds_or_with_a_missing_file(tmp_path: Path) -> None:
    fx = _copy(tmp_path)
    data = json.loads((fx / "controls.json").read_text())
    data["cases"] = [c for c in data["cases"] if c["control"] == "positive"]
    data["cases"][0]["trace"] = "missing.jsonl"
    (fx / "controls.json").write_text(json.dumps(data))
    res = workflow_score.verify_conformance_controls(TEMPLATE, fx)
    assert res.void and any("negative" in p for p in res.problems) and any("missing" in p for p in res.problems)


def test_cli_exits_2_and_writes_the_status_file_on_void(tmp_path: Path) -> None:
    """AC4 status file: a VOID controls run exits 2 and leaves a readable status file."""
    fx = _copy(tmp_path)
    (fx / "controls.json").write_text("{not json")
    status = tmp_path / "status.json"
    rc = workflow_score.main(["--controls", "--root", str(ROOT), "--fixtures", str(fx), "--status-file", str(status)])
    assert rc == 2
    rec = json.loads(status.read_text())
    assert rec["exit"] == 2 and rec["stage"] == "controls" and rec["problems"]


def test_cli_exits_0_on_the_shipped_fixtures(tmp_path: Path) -> None:
    status = tmp_path / "status.json"
    assert workflow_score.main(["--controls", "--root", str(ROOT), "--status-file", str(status)]) == 0
    assert not status.exists()


# --- item 6 / AC5: day markers --------------------------------------------------------------------------------

def test_a_stage_that_exited_2_is_not_paid_for_again_the_same_day(tmp_path: Path) -> None:
    """AC5: the marker is written on exit 2 too, so a second call the same day does not run the stage."""
    calls: list[int] = []

    def stage() -> int:
        calls.append(1)
        return 2

    day = date(2026, 11, 20)
    assert stage_markers.run_stage_once(tmp_path, "judgment", stage, day=day) == 2
    assert stage_markers.run_stage_once(tmp_path, "judgment", stage, day=day) == 2
    assert len(calls) == 1
    assert json.loads((tmp_path / "status.json").read_text())["stage"] == "judgment"
    assert stage_markers.run_stage_once(tmp_path, "judgment", stage, day=day + timedelta(days=1)) == 2
    assert len(calls) == 2


def test_markers_are_per_stage_and_record_exceptions(tmp_path: Path) -> None:
    day = date(2026, 11, 20)
    assert stage_markers.run_stage_once(tmp_path, "sweep", lambda: 0, day=day) == 0
    assert not (tmp_path / "status.json").exists()

    def boom() -> int:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        stage_markers.run_stage_once(tmp_path, "score", boom, day=day)
    assert stage_markers.read_marker(tmp_path, "score", day)["exit"] == 2
    assert stage_markers.run_stage_once(tmp_path, "score", boom, day=day) == 2  # not re-run
    (stage_markers.marker_path(tmp_path, "other", day)).parent.mkdir(parents=True, exist_ok=True)
    stage_markers.marker_path(tmp_path, "other", day).write_text("{bad")
    assert stage_markers.read_marker(tmp_path, "other", day)["exit"] == 2


@pytest.mark.parametrize("bad", ["../x", "", "A", "a/b", "x" * 70])
def test_stage_names_cannot_escape_the_marker_directory(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        stage_markers.marker_path(tmp_path, bad, date(2026, 11, 20))


# --- item 6: live-behavior-watch shows a skip as skipped ------------------------------------------------------

def test_live_behavior_watch_skips_the_job_not_the_steps() -> None:
    """With no key, the live job is skipped by a job-level `if` on the gate job's output, not run green."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github/workflows/live-behavior-watch.yml").read_text(encoding="utf-8"))
    live = wf["jobs"]["live-behavior"]
    assert live["needs"] == "gate" and live["if"] == "needs.gate.outputs.run == 'true'"
    assert all("steps.gate" not in str(s.get("if", "")) for s in live["steps"])
    assert "run" in wf["jobs"]["gate"]["outputs"]


# --- release gate: commit-bound report ------------------------------------------------------------------------

from agentteams.redteam import conformance_report  # noqa: E402

RC = "a" * 40
PANEL = [{"model": "m1", "provider": "p1"}, {"model": "m2", "provider": "p2"}]


def _live() -> list[str]:
    return [w for w, (c, _r) in workflow_spec.COVERAGE.items() if c == workflow_spec.LIVE]


def _report(**over: int) -> dict:
    results = [{"workflow": w, "model": p["model"], "draws": 5, "conforming": over.get(w, 5), "first_deviations": []}
               for w in _live() for p in PANEL]
    return conformance_report.build_conformance_report(RC, {"draws": 5, "min_conforming": 4}, PANEL, results)


def test_a_complete_passing_report_clears_the_gate() -> None:
    """Negative control: every live workflow at threshold for every panel model, bound to the RC, digest intact."""
    report = json.loads(json.dumps(_report()))  # as read back from disk
    assert conformance_report.check_conformance_report(report, RC, TEMPLATE) == []


def test_the_gate_refuses_an_edited_rebound_or_short_report() -> None:
    """Sensitivity: an edit, another commit, a missing workflow or an under-threshold model each refuse."""
    edited = _report()
    edited["results"][0]["conforming"] = 0
    assert any("report_sha256" in p for p in conformance_report.check_conformance_report(edited, RC, TEMPLATE))
    assert any("bound to" in p for p in conformance_report.check_conformance_report(_report(), "b" * 40, TEMPLATE))
    low = _report(**{"7": 3})
    assert any(p.startswith("Workflow 7:") for p in conformance_report.check_conformance_report(low, RC, TEMPLATE))
    short = _report()
    body = {k: v for k, v in short.items() if k != "report_sha256"}
    body["results"] = [r for r in body["results"] if r["workflow"] != "1"]
    rebuilt = conformance_report.build_conformance_report(RC, body["threshold"], body["panel"], body["results"])
    assert "Workflow 1: no result for m1" in conformance_report.check_conformance_report(rebuilt, RC, TEMPLATE)


@pytest.mark.parametrize("commit,threshold", [("abc", {"draws": 5, "min_conforming": 4}),
                                              (RC, {"draws": 5, "min_conforming": 6}),
                                              (RC, {"draws": 5, "min_conforming": 0})])
def test_report_refuses_a_short_sha_or_bad_threshold(commit: str, threshold: dict) -> None:
    with pytest.raises(ValueError):
        conformance_report.build_conformance_report(commit, threshold, PANEL, [])
