"""Governance logs: every row has a writer, and every row matches its log's header (IM item 06).

G-07-1  a HALT written in the instructed 9-column form under the seeded 6-column header was invisible,
        so a later PASS in the header's own form cleared the action (C-2 bypass).
G-07-2  read-only @security was told to append rows.
G-09-1 / G-21-2  read-only @conflict-auditor was told to write the conflict log.
G-09-2  the conflict log was never seeded.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from agentteams import governance_logs, liaison_logs
from agentteams.cli.security_gate import check_clearance

TEMPLATES = Path(__file__).resolve().parents[1] / "agentteams" / "templates"
UNIVERSAL = TEMPLATES / "universal"

_CURRENT = ["date", "plan_slug", "step", "decision", "status", "conditions", "conditions_verified", "evidence", "owner"]


@pytest.fixture(autouse=True)
def _no_signing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTTEAMS_DECISION_SIGNING_KEY", raising=False)


def _seeded_team(tmp_path: Path) -> tuple[Path, Path]:
    out = tmp_path / "proj" / ".claude" / "agents"
    liaison_logs.init_csv_stubs(out / "references")
    return out, out / "references" / "security-decisions.log.csv"


def _append_raw(log: Path, *rows: list[str]) -> None:
    """Append rows the way an agent writing by hand would: a CSV writer, no header check."""
    with log.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        for row in rows:
            writer.writerow(row)


_HALT_9 = ["2026-11-10", "plan-x", "prune", "prune", "HALT", "deletes live data", "", "", "security"]
_PASS_9 = ["2026-11-11", "plan-x", "prune", "prune", "PASS", "", "verified", "", "security"]
_PASS_6 = ["2026-11-11T00:00:00Z", "security", "prune", "PASS", "", "verified"]
_HALT_6 = ["2026-11-10T00:00:00Z", "security", "prune", "HALT", "deletes live data", ""]


# --------------------------------------------------------------------------- G-07-1


def test_g07_1_a_9_column_halt_then_a_6_column_pass_still_blocks(tmp_path):
    """The pinned reproduction. On 70b2c4aa this returned (True, ''): the HALT was invisible."""
    out, log = _seeded_team(tmp_path)
    _append_raw(log, _HALT_9, _PASS_6)
    allowed, reason = check_clearance(out, action="prune")
    assert not allowed
    assert "malformed" in reason and "record 1 has 9 fields, header has 6" in reason


def test_g07_1_a_9_column_halt_then_a_9_column_pass_still_blocks(tmp_path):
    out, log = _seeded_team(tmp_path)
    _append_raw(log, _HALT_9, _PASS_9)
    allowed, reason = check_clearance(out, action="prune")
    assert not allowed and "malformed" in reason


def test_g07_1_a_short_row_under_the_current_header_also_refuses(tmp_path):
    """The reverse mismatch: a 6-column HALT under a 9-column header reads its verdict from `status`."""
    out = tmp_path / "team"
    (out / "references").mkdir(parents=True)
    log = out / "references" / "security-decisions.log.csv"
    log.write_text(",".join(_CURRENT) + "\n", encoding="utf-8")
    _append_raw(log, _HALT_6, _PASS_9)
    allowed, reason = check_clearance(out, action="prune")
    assert not allowed and "record 1 has 6 fields, header has 9" in reason


def test_g07_1_well_formed_logs_keep_their_meaning(tmp_path):
    out, log = _seeded_team(tmp_path)
    _append_raw(log, _PASS_6)
    assert check_clearance(out, action="prune") == (True, "")
    _append_raw(log, _HALT_6, _PASS_6)
    allowed, reason = check_clearance(out, action="prune")
    assert not allowed and "HALT" in reason


def test_g07_1_the_seeded_header_is_the_one_the_template_names_for_a_new_log(tmp_path):
    _, log = _seeded_team(tmp_path)
    header = log.read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == liaison_logs.SECURITY_DECISIONS_HEADERS
    text = (UNIVERSAL / "security.template.md").read_text(encoding="utf-8")
    para = next(ln for ln in text.splitlines() if ln.startswith("**Security Decisions Log**"))
    assert f"A newly generated log has the legacy header `{header}`" in para
    assert "in the columns of the log's own header row" in para
    assert "write new rows in the current 9-column form" not in text


def test_g07_1_validated_append_refuses_a_row_of_the_other_schema(tmp_path):
    _, log = _seeded_team(tmp_path)
    with pytest.raises(ValueError, match="9 fields, header has 6"):
        governance_logs.append_row(log, _HALT_9)
    with pytest.raises(ValueError, match="unknown field"):
        governance_logs.append_row(log, dict(zip(_CURRENT, _HALT_9)))
    assert log.read_text(encoding="utf-8").count("\n") == 1


# --------------------------------------------------------------------------- G-07-2 / G-09-1 / G-21-2


def _front_matter_tools(text: str) -> str:
    m = re.search(r"^tools:\s*(.+)$", text, re.MULTILINE)
    return m.group(1) if m else ""


#: Phrases that tell the agent itself to write a governance log.
_SELF_WRITE = re.compile(
    r"(?i)(\bappend one row\b|\bappend to `|\blog all findings to\b|\blog every finding to\b|"
    r"\blog the verdict to\b|edit no file other than the conflict log|must still produce an entry in the log)"
)


def _read_only_templates() -> list[Path]:
    out = []
    for p in sorted(TEMPLATES.rglob("*.template.md")):
        tools = _front_matter_tools(p.read_text(encoding="utf-8"))
        if tools and not re.search(r"\b(edit|write)\b", tools, re.IGNORECASE):
            out.append(p)
    return out


def test_the_read_only_scan_covers_the_two_auditors():
    names = {p.name for p in _read_only_templates()}
    assert {"security.template.md", "conflict-auditor.template.md"} <= names


@pytest.mark.parametrize("path", _read_only_templates(), ids=lambda p: str(p.relative_to(TEMPLATES)))
def test_no_read_only_agent_is_told_to_write_a_log(path):
    hits = [ln.strip()[:120] for ln in path.read_text(encoding="utf-8").splitlines() if _SELF_WRITE.search(ln)]
    assert not hits, f"{path.name} has tools without edit/write but is told to write: {hits}"


def test_security_returns_its_row_and_the_orchestrator_appends_it():
    sec = (UNIVERSAL / "security.template.md").read_text(encoding="utf-8")
    assert "You are read-only, so you do not write it" in sec
    assert "python -m agentteams.governance_logs append <team dir>/references/security-decisions.log.csv" in sec
    assert "A PASS or CONDITIONAL PASS is in effect only once its row is appended" in sec
    assert "A **HALT binds the moment you return it**" in sec
    assert "A verdict is in effect only once" not in sec
    orch = (UNIVERSAL / "orchestrator.template.md").read_text(encoding="utf-8")
    block = orch[orch.index("**Rows returned by read-only agents.**"):]
    block = block[:block.index("\n\n")]
    assert "`@security` and `@conflict-auditor` cannot write files" in block
    assert "python -m agentteams.governance_logs append <log>" in block
    assert "CONDITIONAL PASS is not in effect" in block
    assert "A HALT binds the moment\n`@security` returns it" in block


def test_conflict_auditor_returns_rows_including_a_clean_row():
    ca = (UNIVERSAL / "conflict-auditor.template.md").read_text(encoding="utf-8")
    assert "You are read-only and do not write the log" in ca
    assert "`code` = `CLEAN`" in ca and "`status` = `closed`" in ca
    assert "and edit no file (your conflict-log rows are returned, and the orchestrator appends them)" in ca


# --------------------------------------------------------------------------- G-09-2


def test_g09_2_the_conflict_log_is_seeded_with_the_template_columns(tmp_path):
    refs = tmp_path / "references"
    created = liaison_logs.init_csv_stubs(refs)
    assert "conflict-log.csv" in created
    header = (refs / "conflict-log.csv").read_text(encoding="utf-8").strip()
    ca = (UNIVERSAL / "conflict-auditor.template.md").read_text(encoding="utf-8")
    m = re.search(r"use the columns\n`([a-z_,]+)`", ca)
    assert m, "Conflict Log Format column list not found"
    assert header == m.group(1) == ",".join(liaison_logs.CONFLICT_LOG_HEADERS)


def test_g09_2_an_existing_conflict_log_is_never_touched(tmp_path):
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "conflict-log.csv").write_text("date,category\n2026-01-01,x\n", encoding="utf-8")
    assert "conflict-log.csv" not in liaison_logs.init_csv_stubs(refs)
    assert (refs / "conflict-log.csv").read_text(encoding="utf-8") == "date,category\n2026-01-01,x\n"


# --------------------------------------------------------------------------- the append CLI


def test_cli_append_validates_against_the_header(tmp_path, capsys):
    refs = tmp_path / "references"
    liaison_logs.init_csv_stubs(refs)
    log = refs / "conflict-log.csv"
    row = {"date": "2026-11-10", "category": "consistency", "code": "CLEAN", "severity": "",
           "file": "", "description": "audited plan X", "status": "closed", "resolution": "clean audit"}
    assert governance_logs.main(["append", str(log), "--row-json", json.dumps(row)]) == 0
    assert "2026-11-10,consistency,CLEAN" in log.read_text(encoding="utf-8")
    assert governance_logs.main(["append", str(log), "--row-json", json.dumps({"bogus": "x"})]) == 1
    assert "unknown field" in capsys.readouterr().err
    assert governance_logs.main(["append", str(log), "--row-json", "not json"]) == 1
    assert governance_logs.main(["check", str(tmp_path)]) == 0


def test_cli_check_reports_a_wrong_width_row(tmp_path):
    out, log = _seeded_team(tmp_path)
    _append_raw(log, _HALT_9)
    assert governance_logs.main(["check", str(out)]) == 1


# --------------------------------------------------------------------------- audit follow-ups


def test_an_open_quote_that_swallows_a_halt_refuses(tmp_path):
    """A quote left open in a last column swallows the HALT after it without changing any field count."""
    out, log = _seeded_team(tmp_path)
    with log.open("a", encoding="utf-8", newline="") as fh:
        fh.write('2026-11-09T00:00:00Z,security,prune,PASS,,"verified\n')
        fh.write("2026-11-10T00:00:00Z,security,prune,HALT,deletes live data,\n")
    allowed, reason = check_clearance(out, action="prune")
    assert not allowed and "malformed" in reason


def test_a_duplicated_header_name_refuses(tmp_path):
    out = tmp_path / "team"
    (out / "references").mkdir(parents=True)
    log = out / "references" / "security-decisions.log.csv"
    log.write_text("timestamp,requesting_agent,action_reviewed,verdict,verdict,conditions,conditions_verified\n"
                   "2026-11-10T00:00:00Z,security,prune,HALT,PASS,,verified\n", encoding="utf-8")
    allowed, reason = check_clearance(out, action="prune")
    assert not allowed and "more than once" in reason


def test_a_padded_header_is_read_by_its_stripped_names(tmp_path):
    out = tmp_path / "team"
    (out / "references").mkdir(parents=True)
    log = out / "references" / "security-decisions.log.csv"
    log.write_text("timestamp, requesting_agent, action_reviewed, verdict, conditions, conditions_verified\n"
                   "2026-11-10T00:00:00Z,security,prune,PASS,,verified\n"
                   "2026-11-11T00:00:00Z,security,prune,HALT,stop,\n", encoding="utf-8")
    allowed, reason = check_clearance(out, action="prune")
    assert not allowed and "HALT" in reason


@pytest.mark.parametrize("row", [
    {"timestamp": "2026-11-10T00:00:00Z", "requesting_agent": "security", "verdict": "HALT"},
    {"timestamp": "2026-11-10T00:00:00Z", "requesting_agent": "security", "action_reviewed": "prune"},
])
def test_append_refuses_a_decision_row_without_action_or_verdict(tmp_path, row):
    _, log = _seeded_team(tmp_path)
    with pytest.raises(ValueError, match="needs its action id and its verdict"):
        governance_logs.append_row(log, row)


def test_pre_execution_check_appends_a_new_pass_rather_than_editing():
    orch = (UNIVERSAL / "orchestrator.template.md").read_text(encoding="utf-8")
    pre = orch[orch.index("### Pre-Execution Security Check"):]
    pre = pre[:pre.index("---")]
    assert "no row means no clearance (treat as HALT)" in pre
    assert "never edit the existing row" in pre
    assert "update `conditions_verified` to `verified` in the log" not in pre


@pytest.mark.parametrize(("framework", "path"), [
    ("copilot-vscode", ".github/agents/references/conflict-log.csv"),
    ("claude", ".claude/agents/references/conflict-log.csv"),
    ("goose", ".goose/recipes/references/conflict-log.csv"),
    ("codex", ".codex/agents/references/conflict-log.csv"),
])
def test_g09_2_the_conflict_log_path_is_the_teams_own_references_dir(framework, path):
    from agentteams.frameworks.registry import conflict_log_path_for
    from agentteams.enrich._fills import _fill_conflict_log_path

    assert conflict_log_path_for(framework) == path
    assert _fill_conflict_log_path({"framework": framework}) == path


def test_g09_2_a_claude_render_points_the_auditor_at_the_seeded_log():
    from agentteams import analyze, ingest, render

    repo = Path(__file__).resolve().parents[1]
    desc = ingest.load(repo / "examples/software-project/brief.json", scan_project=False)
    manifest = analyze.build_manifest(desc, framework="claude")
    body = next(c for r, c in render.render_all(manifest, templates_dir=TEMPLATES) if r.endswith("conflict-auditor.agent.md")
                or r.endswith("conflict-auditor.md"))
    assert ".claude/agents/references/conflict-log.csv" in body
    assert ".github/agents/references/conflict-log.csv" not in body


def test_append_refuses_a_row_that_would_break_a_chained_log(tmp_path):
    from agentteams.cli.decision_chain import row_chain_digest

    log = tmp_path / "references" / "security-decisions.log.csv"
    log.parent.mkdir(parents=True)
    header = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions",
              "conditions_verified", "prev_digest"]
    first = dict(zip(header, ["2026-11-01T00:00:00Z", "security", "prune", "PASS", "", "verified", ""]))
    second = dict(zip(header, ["2026-11-02T00:00:00Z", "security", "prune", "PASS", "", "verified",
                               row_chain_digest(first)]))
    with log.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows([first, second])
    with pytest.raises(ValueError, match="--sign-decision"):
        governance_logs.append_row(log, {"timestamp": "t", "requesting_agent": "security",
                                         "action_reviewed": "prune", "verdict": "HALT"})
    linked = {"timestamp": "t", "requesting_agent": "security", "action_reviewed": "prune",
              "verdict": "HALT", "prev_digest": row_chain_digest(second)}
    governance_logs.append_row(log, linked)
