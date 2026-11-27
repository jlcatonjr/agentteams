"""Tests for agentteams.gate_catch_ledger — the read-only, counts-only gate catch ledger.

Every fixture here is synthetic. The live governance logs are private and are never read:
``test_the_tests_never_read_the_live_logs`` pins that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import gate_catch_ledger as ledger_mod

REPO_ROOT = Path(__file__).resolve().parents[1]
TEAM = Path(".github") / "agents"


def _refs(base: Path, team: Path = TEAM) -> Path:
    """Create and return a synthetic team's references directory."""
    refs = base / team / "references"
    refs.mkdir(parents=True, exist_ok=True)
    return refs


def _build(base: Path) -> dict:
    """Build the ledger for a synthetic repository with at most one team."""
    return ledger_mod.build_ledger(base, ledger_mod.resolve_team_dir(base))


SECRET_CELLS = (
    "SYNTHETIC-DESCRIPTION-ZQ1",
    "SYNTHETIC-CONDITION-ZQ2",
    "SYNTHETIC-EVIDENCE-ZQ3",
    "synthetic/file/zq4.py",
    "SENTINEL-STATUS-ZQ5",
    "SENTINEL-SEVERITY-ZQ6",
    "SENTINEL-STATUS-ZQ7",
)

SECURITY_CSV = (
    "date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner\n"
    "2031-01-02,p,1,d1,PASS,SYNTHETIC-CONDITION-ZQ2,yes,SYNTHETIC-EVIDENCE-ZQ3,@security\n"
    "2031-01-03,p,2,d2,CONDITIONAL PASS,c,pending,e,@security\n"
    "2031-01-04,p,3,d3,CONDITIONAL-PASS,c,pending,e,@security\n"
    "2031-02-05,p,4,d4,HALT,c,,e,@security\n"
    "2031-02-06,p,5,d5,cleared,c,,e,@security\n"
    "2031-02-07,p,6,d6,closed,c,,e,@security\n"
    "not-a-date,p,7,d7,recorded,c,,e,@security\n"
)
# Three more rows whose verdict leads the decision cell while status holds bookkeeping.
SECURITY_DECISION_ROWS = (
    "2031-03-01,p,8,HALT: refused,closed,c,,e,@security\n"
    "2031-03-02,p,9,CONDITIONAL PASS on review,cleared,c,,e,@security\n"
    "2031-03-03,p,10,PASS after fixes,recorded,c,,e,@security\n"
    "2031-03-04,p,11,HALT-RETRACTED,HALT-RETRACTED,c,,e,@security\n"
    "2031-03-05,p,12,d12,SENTINEL-STATUS-ZQ5,c,,e,@security\n"
)
LEGACY_SECURITY_CSV = (
    "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified,consumed\n"
    "2031-04-01T10:00:00Z,@a,x,PASS,c,,\n"
    "2031-04-02T10:00:00Z,@a,y,HALT,c,,\n"
)

CONFLICT_CSV = (
    "date,category,code,severity,file,description,status,resolution\n"
    "2031-01-02,CLAIM_CONFLICT,CC1,HIGH,synthetic/file/zq4.py,SYNTHETIC-DESCRIPTION-ZQ1,RESOLVED,fixed\n"
    "2031-01-03,CONSISTENCY,CO1,low,f,d,open,\n"
    "2031-01-04,CLEAN,CL1,info,f,d,closed,\n"
    "2031-01-05,CONSISTENCY,CLEAN,info,f,d,closed,\n"
    "2031-02-01,CLAIM_CONFLICT,CC2,low,f,Clean audit of the plan. No findings.,closed,\n"
    "2031-02-02,STALENESS,ST1,Medium,f,d,closed,No action needed: the cited row was accurate\n"
)
CONFLICT_EXTRA_ROWS = (
    "2031-03-01,NONE,OK,none,f,d,closed,\n"
    "2031-03-02,CONFLICT_AUDIT,CA1,info,f,scope note,closed,\n"
    "2031-03-03,CLAIM_CONFLICT,CC3,low,f,d,closed,Clean audit on re-check\n"
    "2031-03-04,CONSISTENCY,CO2,SENTINEL-SEVERITY-ZQ6,f,d,SENTINEL-STATUS-ZQ7,retracted\n"
)

STEPS_CSV = (
    "step,agent,action,inputs,outputs,status,notes\n"
    "1,@primary-producer,write,a,b,done,\n"
    "2,@adversarial + Conflict Auditor,audit,a,b,done,\n"
    "3,@security,review,a,b,pending,\n"
    "4,@conflict-auditor,audit,a,b,pending,\n"
)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """A synthetic repository root holding one team with both logs and two plan files."""
    refs = _refs(tmp_path)
    (refs / "security-decisions.log.csv").write_text(SECURITY_CSV, encoding="utf-8")
    (refs / "conflict-log.csv").write_text(CONFLICT_CSV, encoding="utf-8")
    week = tmp_path / "tmp" / "by-week" / "2031-W05"
    week.mkdir(parents=True)
    (week / "one.steps.csv").write_text(STEPS_CSV, encoding="utf-8")
    (week / "two.steps.csv").write_text(STEPS_CSV, encoding="utf-8")
    stray = tmp_path / "tmp" / "by-week" / "not-a-week"
    stray.mkdir()
    (stray / "ignored.steps.csv").write_text(STEPS_CSV, encoding="utf-8")
    return tmp_path


def test_rule_of_three_bounds_a_clean_record() -> None:
    assert ledger_mod.rule_of_three(30) == pytest.approx(0.1)
    assert ledger_mod.rule_of_three(300) == pytest.approx(0.01)
    assert ledger_mod.rule_of_three(2) == 1.0
    assert ledger_mod.rule_of_three(0) is None
    assert ledger_mod.rule_of_three(-5) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("PASS", "pass"),
        (" cleared ", "pass"),
        ("CONDITIONAL PASS", "conditional"),
        ("conditional-pass", "conditional"),
        ("CONDITIONAL", "conditional"),
        ("HALT", "halt"),
        ("PASS-WITH-FIXES", "conditional"),
        ("PASS (conditions met)", "pass"),
        ("CLEARED-WITH-CONDITIONS", "conditional"),
        ("APPROVE-WITH-CONDITIONS", "conditional"),
        ("authorization-cleared", "pass"),
        ("halted", "halt"),
        ("blocked", "halt"),
        ("FAIL", "halt"),
        ("HALT-RETRACTED", "other"),
        ("closed", "other"),
        ("PASSWORD", "other"),
        ("", "other"),
    ],
)
def test_security_status_classes(status: str, expected: str) -> None:
    assert ledger_mod.classify_security_status(status) == expected


@pytest.mark.parametrize(
    ("status", "decision", "expected"),
    [
        ("closed", "HALT: refused", "halt"),
        ("cleared", "CONDITIONAL PASS on review", "conditional"),
        ("cleared", "HALT", "halt"),
        ("cleared", "some-slug", "pass"),
        ("recorded", "PASS after fixes", "pass"),
        ("PASS", "CONDITIONAL PASS", "pass"),
        ("HALT-RETRACTED", "HALT-RETRACTED", "other"),
        ("HALT RETRACTED", "HALT: refused", "other"),
        ("open", "merge-gate-slug", "other"),
    ],
)
def test_a_verdict_in_the_decision_cell_is_read_when_status_is_bookkeeping(
    status: str, decision: str, expected: str
) -> None:
    assert ledger_mod.classify_security_status(status, decision) == expected


def test_decision_cell_verdicts_and_retractions_are_counted_correctly(tmp_path: Path) -> None:
    log = _refs(tmp_path) / "security-decisions.log.csv"
    log.write_text(SECURITY_CSV + SECURITY_DECISION_ROWS, encoding="utf-8")
    sec = _build(tmp_path)["security"]
    assert (sec["halt"], sec["conditional"], sec["pass"]) == (2, 3, 3)
    assert sec["unclassified"] == 4  # closed, recorded, the retraction, the sentinel
    assert set().union(*sec["by_month"].values()) <= set(ledger_mod.VERDICT_CLASSES)


def test_the_legacy_verdict_schema_is_read(tmp_path: Path) -> None:
    (_refs(tmp_path) / "security-decisions.log.csv").write_text(LEGACY_SECURITY_CSV, encoding="utf-8")
    sec = _build(tmp_path)["security"]
    assert (sec["halt"], sec["pass"], sec["firings"]) == (1, 1, 2)
    assert list(sec["by_month"]) == ["2031-04"]


def test_a_repository_with_two_teams_is_refused_not_guessed(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = _refs(root, Path(".claude") / "agents")
    (other / "security-decisions.log.csv").write_text(LEGACY_SECURITY_CSV, encoding="utf-8")
    with pytest.raises(ValueError, match="more than one team directory"):
        ledger_mod.resolve_team_dir(root)
    assert ledger_mod.main(["--root", str(root)]) == 2
    assert "pass --framework or --team-dir" in capsys.readouterr().err


def test_framework_and_team_dir_select_the_team(root: Path) -> None:
    other = _refs(root, Path(".claude") / "agents")
    (other / "security-decisions.log.csv").write_text(LEGACY_SECURITY_CSV, encoding="utf-8")
    claude = ledger_mod.build_ledger(root, ledger_mod.resolve_team_dir(root, framework="claude"))
    assert claude["security"]["firings"] == 2 and claude["conflict_auditor"] is None
    assert claude["coverage"]["conflict_log"] == "absent"
    copilot = ledger_mod.build_ledger(root, ledger_mod.resolve_team_dir(root, team_dir=TEAM))
    assert copilot["security"]["firings"] == 5 and copilot["conflict_auditor"] is not None
    with pytest.raises(ValueError):
        ledger_mod.resolve_team_dir(root, framework="no-such-framework")


def test_a_named_team_directory_that_does_not_exist_is_a_usage_error(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(ValueError, match="team directory not found: no/such/dir"):
        ledger_mod.resolve_team_dir(root, team_dir=Path("no/such/dir"))
    assert ledger_mod.main(["--root", str(root), "--framework", "goose"]) == 2
    assert ledger_mod.main(["--root", str(root), "--team-dir", "no/such/dir"]) == 2
    assert str(root) not in capsys.readouterr().err, "errors name directories relative to the root"


def test_the_ambiguity_error_names_no_absolute_path(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = _refs(root, Path(".claude") / "agents")
    (other / "conflict-log.csv").write_text(CONFLICT_CSV, encoding="utf-8")
    assert ledger_mod.main(["--root", str(root)]) == 2
    message = capsys.readouterr().err
    assert ".claude/agents" in message and ".github/agents" in message
    assert str(root) not in message and str(Path.home()) not in message


def test_a_log_with_no_recognisable_verdict_has_no_denominator(tmp_path: Path) -> None:
    log = _refs(tmp_path) / "security-decisions.log.csv"
    log.write_text(SECURITY_CSV.splitlines()[0] + "\n2031-01-01,p,1,d,recorded,c,,e,@security\n", encoding="utf-8")
    result = _build(tmp_path)
    assert result["security"] is None and result["coverage"]["security_log"] == "found"
    assert result["coverage"]["gates_with_denominator"] == []


def test_an_explicit_security_log_replaces_the_team_log(root: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.csv"
    elsewhere.write_text(LEGACY_SECURITY_CSV, encoding="utf-8")
    result = ledger_mod.build_ledger(root, root / TEAM, security_log=elsewhere)
    assert result["security"]["firings"] == 2
    assert result["conflict_auditor"]["claimed_catches"] == 3


def test_severity_and_status_are_closed_label_sets(root: Path) -> None:
    log = root / TEAM / "references" / "conflict-log.csv"
    log.write_text(CONFLICT_CSV + CONFLICT_EXTRA_ROWS, encoding="utf-8")
    con = _build(root)["conflict_auditor"]
    # Three from the base fixture, plus NONE/OK and CONFLICT_AUDIT. A finding whose *resolution*
    # mentions a clean audit stays a finding: a later clean re-check does not erase the catch.
    assert con["clean_audit_rows"] == 5
    assert con["claimed_catches"] == 5
    assert con["resolved_as_false"] == 2
    assert set(con["by_severity"]) <= set(ledger_mod.SEVERITY_LABELS.values()) | {"other"}
    assert con["by_severity"]["other"] == 1
    assert set(con["by_status"]) <= {"open", "closed", "other"}
    assert con["by_status"]["other"] == 1


def test_an_unreadable_log_is_not_reported_as_absent(root: Path) -> None:
    log = root / TEAM / "references" / "security-decisions.log.csv"
    log.unlink()
    log.mkdir()  # something is there, but it cannot be read as a log
    result = _build(root)
    assert result["coverage"]["security_log"] == "unreadable"
    assert result["security"] is None
    assert result["coverage"]["gates_with_denominator"] == []
    assert "UNREADABLE" in ledger_mod.render_markdown(result)


def test_rows_read_before_a_csv_failure_are_kept(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_reader = ledger_mod.csv.DictReader

    def failing_reader(handle: object) -> object:
        for index, row in enumerate(real_reader(handle)):
            if index == 2:
                raise ledger_mod.csv.Error("synthetic failure")
            yield row

    monkeypatch.setattr(ledger_mod.csv, "DictReader", failing_reader)
    state, rows = ledger_mod._read_rows(root / TEAM / "references" / "security-decisions.log.csv")
    assert state == "unreadable" and len(rows) == 2


def test_an_empty_conflict_log_renders(tmp_path: Path) -> None:
    (_refs(tmp_path) / "conflict-log.csv").write_text(CONFLICT_CSV.splitlines()[0] + "\n", encoding="utf-8")
    result = _build(tmp_path)
    assert result["coverage"]["conflict_log"] == "found" and result["conflict_auditor"] is None
    assert "No data." in ledger_mod.render_markdown(result)


def test_security_ledger_counts_only_verdicts_as_firings(root: Path) -> None:
    sec = _build(root)["security"]
    assert (sec["halt"], sec["conditional"], sec["pass"], sec["unclassified"]) == (1, 2, 2, 2)
    assert sec["firings"] == 5
    assert sec["halt_rate"] == pytest.approx(0.2)
    assert sec["halt_rate_upper_bound_if_zero"] is None
    assert sec["by_month"]["2031-02"] == {"halt": 1, "pass": 1, "other": 1}
    assert "unknown" in sec["by_month"]


def test_a_clean_security_record_gets_a_rule_of_three_bound(tmp_path: Path) -> None:
    rows = "".join(f"2031-01-{d:02d},p,{d},d,PASS,c,,e,@security\n" for d in range(1, 31))
    log = _refs(tmp_path) / "security-decisions.log.csv"
    log.write_text(SECURITY_CSV.splitlines()[0] + "\n" + rows, encoding="utf-8")
    sec = _build(tmp_path)["security"]
    assert sec["halt"] == 0 and sec["firings"] == 30
    assert sec["halt_rate_upper_bound_if_zero"] == pytest.approx(0.1)


def test_conflict_ledger_separates_clean_rows_and_false_catches(root: Path) -> None:
    con = _build(root)["conflict_auditor"]
    assert con["clean_audit_rows"] == 3  # category CLEAN, code CLEAN, and prose-only
    assert con["claimed_catches"] == 3
    assert con["resolved_as_false"] == 1
    assert con["by_severity"] == {"high": 1, "low": 1, "medium": 1}
    assert con["by_status"] == {"closed": 2, "open": 1}
    assert con["firings"] is None, "the conflict log has no reliable denominator and must not claim one"


def test_plan_ledger_counts_gate_mentions_and_skips_non_week_folders(root: Path) -> None:
    plans = _build(root)["plans"]
    assert plans["plans"] == 2 and plans["steps"] == 8
    assert plans["weeks_covered"] == ["2031-W05"]
    assert plans["gate_steps"]["conflict-auditor"] == 4
    assert plans["gate_steps"]["adversarial"] == 2
    assert plans["gate_steps"]["security"] == 2
    assert plans["gate_steps"]["code-hygiene"] == 0


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("@conflict-auditor", {"conflict-auditor"}),
        ("Conflict Auditor", {"conflict-auditor"}),
        ("code_hygiene / Technical Validator", {"code-hygiene", "technical-validator"}),
        ("@security-maintenance", set()),
        ("@primary-producer", set()),
    ],
)
def test_gate_names_tolerate_spacing_and_case(cell: str, expected: set[str]) -> None:
    assert ledger_mod.gates_named(cell) == expected


def test_missing_logs_are_reported_not_raised(tmp_path: Path) -> None:
    result = _build(tmp_path)
    assert result["security"] is None and result["conflict_auditor"] is None
    assert result["coverage"]["security_log"] == "absent"
    assert result["coverage"]["team_dir_found"] is False
    assert result["plans"] is None and result["coverage"]["plan_steps"] == "absent"
    rendered_plans = ledger_mod.render_markdown(result)
    assert "No readable plan step files found: no denominator." in rendered_plans and "| @security | 0 |" not in rendered_plans
    assert result["coverage"]["gates_with_denominator"] == []
    assert result["coverage"]["gates_without_denominator"][0] == "security"
    rendered = ledger_mod.render_markdown(result)
    assert "ABSENT" in rendered and "Gates without a denominator: security, conflict-auditor" in rendered
    assert f"- Generated: {result['coverage']['generated']}" in rendered


def test_output_never_carries_a_log_cell(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Counts only: no cell of any log, status and severity included, may reach the output."""
    security_log = root / TEAM / "references" / "security-decisions.log.csv"
    security_log.write_text(SECURITY_CSV + SECURITY_DECISION_ROWS, encoding="utf-8")
    conflict_log = root / TEAM / "references" / "conflict-log.csv"
    conflict_log.write_text(CONFLICT_CSV + CONFLICT_EXTRA_ROWS, encoding="utf-8")
    assert ledger_mod.main(["--root", str(root)]) == 0
    markdown = capsys.readouterr().out
    assert ledger_mod.main(["--root", str(root), "--json"]) == 0
    as_json = capsys.readouterr().out
    json.loads(as_json)
    for cell in SECRET_CELLS:
        assert cell not in markdown
        assert cell not in as_json


def test_it_writes_only_when_given_an_output_path(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    assert ledger_mod.main(["--root", str(root)]) == 0
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before
    capsys.readouterr()
    target = tmp_path / "out" / "ledger.md"
    target.parent.mkdir()
    assert ledger_mod.main(["--root", str(root), "--output", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("# Gate catch ledger")
    assert capsys.readouterr().out == ""


def test_an_unwritable_output_path_exits_nonzero(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ledger_mod.main(["--root", str(root), "--output", str(root / "no-such-dir" / "x.md")]) == 1
    assert "could not write" in capsys.readouterr().err


def test_the_tests_never_read_the_live_logs() -> None:
    """The live logs are private. This file must build every fixture itself and pass ``--root``."""
    source = Path(__file__).read_text(encoding="utf-8")
    call = "ledger_mod." + "main(["
    calls = source.count(call)
    assert calls and source.count(call + '"--root"') == calls
    assert ("build_ledger(" + "REPO_ROOT") not in source
