"""Tests for ``agentteams --mcp-need-report`` (agentteams/mcp_need_report.py, MCP-need protocol phase N3)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import mcp_need, mcp_need_report as R
from agentteams import proposals

REPO = Path(__file__).resolve().parent.parent


def _ledger(root: Path, bodies: list[dict], *, head: bool = True) -> list[str]:
    """Write a properly chained ledger (dummy signatures) and its head anchor, as the runner would."""
    path = root / R.LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    lines, prev = [], ""
    for body in bodies:
        line = json.dumps(dict(body, prev=prev, mac="x"), sort_keys=True)
        lines.append(line)
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if head:
        (root / R.HEAD_REL).write_text(json.dumps({"count": len(lines), "last": prev, "mac": "x"}) + "\n",
                                       encoding="utf-8")
    return lines


def _register(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(mcp_need.MCP_NEEDS_HEADERS)]
    for r in rows:
        lines.append(",".join(r.get(h, "") for h in mcp_need.MCP_NEEDS_HEADERS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


LEAN = ["lake", "env", "lean", "--stdin"]
ROWS = [
    {"action": "run-request-start", "agent": "lean-prover", "argv": LEAN},
    {"action": "run-request", "agent": "lean-prover", "argv": LEAN, "exit": 0, "duration_ms": 100},
    {"action": "run-request-start", "agent": "lean-prover", "argv": LEAN},
    {"action": "run-request", "agent": "lean-prover", "argv": LEAN, "exit": 1, "duration_ms": 300},
    {"action": "run-request-start", "agent": "lean-prover", "argv": LEAN},
    {"action": "run-request-timeout", "agent": "lean-prover", "argv": LEAN, "duration_ms": 900},
    {"action": "run-request-start", "agent": "lean-prover", "argv": ["lake", "build", "X"]},
    {"action": "run-request", "agent": "lean-prover", "argv": ["lake", "build", "X"], "exit": 0, "duration_ms": 5},
    {"action": "apply-proposal", "agent": "lean-prover", "bytes": 1000},
    {"action": "apply-proposal", "agent": "lean-prover", "bytes": 3000},
    {"action": "apply-proposal-refused", "agent": "fidelity-auditor", "reason": "outside write_scopes"},
    {"action": "issue-dispatch", "agent": "lean-prover", "id": "abc"},
]


def test_paths_match_proposals():
    # Restated to avoid importing the POSIX-only proposals module at report time; they must not drift.
    assert (R.LEDGER_REL, R.HEAD_REL, R.DISPATCH_REL) == (proposals.LEDGER_REL, proposals.HEAD_REL,
                                                          proposals.DISPATCH_REL)


def test_repeated_commands_grouped_and_timed(tmp_path):
    _ledger(tmp_path, ROWS)
    rep = R.build_report(tmp_path)
    assert rep["chain_problems"] == [] and rep["signatures_verified"] is False
    lp = rep["agents"]["lean-prover"]
    assert lp["commands"] == [{"prefix": ["lake", "env", "lean"], "count": 3, "total_ms": 1300,
                               "median_ms": 300, "failed": 2}]       # `lake build X` ran once: not repeated
    assert lp["proposals"] == {"count": 2, "bytes_total": 4000, "bytes_median": 2000}
    assert lp["unfinished"] == 0
    assert rep["agents"]["fidelity-auditor"]["refused"] == 1


def test_undeclared_writes_and_unfinished_runs(tmp_path):
    _ledger(tmp_path, [
        {"action": "run-request-start", "agent": "a", "argv": ["x"]},
        {"action": "run-request", "agent": "a", "argv": ["x"], "exit": 0, "duration_ms": 1, "undeclared_writes": ["f"]},
        {"action": "run-request-start", "agent": "a", "argv": ["x"]},
        {"action": "run-request", "agent": "a", "argv": ["x"], "exit": 0, "duration_ms": 1},
        {"action": "run-request-start", "agent": "a", "argv": ["x"]},                 # never closed
    ])
    a = R.build_report(tmp_path)["agents"]["a"]
    assert a["commands"][0]["failed"] == 1 and a["unfinished"] == 1


@pytest.mark.parametrize("tamper, expect", [
    (lambda root, lines: (root / R.LEDGER_REL).write_text("\n".join(lines[:3] + lines[4:]) + "\n"), "chain"),
    (lambda root, lines: (root / R.LEDGER_REL).write_text("\n".join(lines[:-2]) + "\n"), "counts"),
    (lambda root, lines: (root / R.HEAD_REL).unlink(), "head anchor is missing"),
    (lambda root, lines: (root / R.LEDGER_REL).write_text(
        "\n".join(lines) + "\n" + json.dumps({"action": "x", "prev": "", "agent": "a"}) + "\n"), "no signature"),
])
def test_keyless_chain_and_head_checks(tmp_path, tamper, expect):
    lines = _ledger(tmp_path, ROWS)
    tamper(tmp_path, lines)
    problems = R.check_chain(tmp_path)
    assert any(expect in p for p in problems), problems
    assert R.format_report(R.build_report(tmp_path)).startswith("chain/head: NOT CONSISTENT")


def test_removed_ledger_with_dispatches_is_flagged(tmp_path):
    (tmp_path / ".agentteams").mkdir()
    (tmp_path / R.DISPATCH_REL).write_text("{}\n", encoding="utf-8")
    assert R.check_chain(tmp_path) == ["ledger and head are missing but dispatch records exist (ledger removed)"]
    assert R.run(tmp_path) == 1


def test_inserted_blank_line_breaks_the_chain(tmp_path):
    lines = _ledger(tmp_path, ROWS)
    (tmp_path / R.LEDGER_REL).write_text("\n".join(lines[:2] + [""] + lines[2:]) + "\n", encoding="utf-8")
    assert R.check_chain(tmp_path)


def test_dangling_symlink_is_refused_not_missing(tmp_path):
    (tmp_path / ".agentteams").mkdir()
    (tmp_path / R.LEDGER_REL).symlink_to(tmp_path / "nowhere.jsonl")
    with pytest.raises(R.ReportPathError, match="symlink"):
        R.build_report(tmp_path)


def test_unreadable_rows_counted_not_fatal(tmp_path):
    lines = _ledger(tmp_path, ROWS[:2])
    (tmp_path / R.LEDGER_REL).write_text("\n".join(lines) + "\nnot json\n[1, 2]\n", encoding="utf-8")
    rep = R.build_report(tmp_path, min_repeats=1)
    assert rep["unreadable_rows"] == 2 and rep["agents"]["lean-prover"]["commands"][0]["count"] == 1
    assert any("not a JSON object" in p for p in rep["chain_problems"])


def test_register_label_is_the_registers_own_claim(tmp_path):
    _ledger(tmp_path, ROWS)
    _register(tmp_path / ".claude" / "agents" / "references" / mcp_need.MCP_NEEDS_CSV, [
        {"id": "lean-read", "agent": "lean-prover", "capability": "goal state", "verified": "no",
         "decision": "USE_RUNNER_PATH", "status": "waiting"},
        {"id": "mathlib-search", "agent": "lean-prover", "capability": "name search", "verified": "yes",
         "decision": "PROPOSE_KIND_1", "status": "proposed"},
        {"id": "old", "agent": "lean-prover", "capability": "x", "verified": "yes", "status": "retired"},
    ])
    rep = R.build_report(tmp_path)
    reg = rep["agents"]["lean-prover"]["register"]
    assert [r["id"] for r in reg["unverified"]] == ["lean-read"] and [r["id"] for r in reg["verified"]] == [
        "mathlib-search"]                                                   # retired rows are not open
    assert "[register says verified] mathlib-search" in R.format_report(rep)


def test_untrusted_text_is_escaped_and_capped(tmp_path):
    hostile = "\x1b[2J" + "z" * 1000                                       # a terminal escape and an oversized field
    _ledger(tmp_path, [{"action": "run-request", "agent": "a", "argv": [hostile], "exit": 0, "duration_ms": 1}] * 2)
    text = R.format_report(R.build_report(tmp_path))
    assert "\x1b" not in text and "\\x1b" in text
    assert max(len(line) for line in text.splitlines()) < R.MAX_FIELD + 120


def test_symlinked_or_outside_paths_are_refused(tmp_path):
    outside = tmp_path / "outside.csv"
    _register(outside, [{"id": "x", "agent": "a", "status": "open"}])
    project = tmp_path / "p"
    _ledger(project, ROWS)
    with pytest.raises(R.ReportPathError, match="outside the project"):
        R.build_report(project, register=outside)
    link = project / "references" / mcp_need.MCP_NEEDS_CSV
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    with pytest.raises(R.ReportPathError, match="symlink"):
        R.build_report(project)
    assert R.run(project) == 1


def test_text_report_lists_runner_path_fix_first_and_decides_nothing(tmp_path):
    _ledger(tmp_path, ROWS)
    text = R.format_report(R.build_report(tmp_path))
    assert text.startswith("chain/head: consistent") and "decides nothing" in text
    assert text.index("repeated: lake env lean") < text.index("first try a runner-path fix")


def test_no_ledger_is_an_empty_report(tmp_path):
    rep = R.build_report(tmp_path)
    assert rep["ledger"] is None and rep["agents"] == {} and rep["chain_problems"] == []
    assert "No agent activity" in R.format_report(rep)


def test_cli_is_read_only_and_supports_json(tmp_path):
    _ledger(tmp_path, ROWS)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--mcp-need-report", "--project",
                           str(tmp_path), "--json"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["agents"]["lean-prover"]["proposals"]["bytes_total"] == 4000
    assert {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")} == before     # nothing written or touched
