"""M6-R4: a Goose team's ``security-decisions.log.csv`` is append-only.

mathAgents' M6 run saw existing decision rows come back re-quoted (same values, different bytes).
The Goose-specific writer was the shipped coordination MCP server (``scripts/goose-coordination-mcp.py``,
emitted into teams that declare cross-repo coordination): its ``request_security_clearance`` tool
re-parsed the whole log and wrote every row back through ``csv.writer``. It now appends one row and
leaves the existing bytes alone. A re-render (``--update --merge``) must not touch the log either.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import importlib.util
import io
from pathlib import Path

import pytest

import build_team

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "examples" / "research-project" / "brief.json"
_SERVER_PATH = ROOT / "scripts" / "goose-coordination-mcp.py"
_WAIVER_KEY = "goose-decision-log-waiver-key-0123"

#: Hand-written rows a CSV writer would render differently: every field quoted, a quoted comma.
HAND_ROWS = (
    '"2026-10-20T00:00:00Z","security","hand-review","PASS","none","verified"\n'
    '2026-10-20T00:00:01Z,security,"scope, with comma",REQUESTED,"",no\n'
)


def _load_server():
    spec = importlib.util.spec_from_file_location("goose_coordination_mcp_m6r4", _SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _request_clearance(mod, refs: Path) -> dict:
    return mod.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "request_security_clearance",
                    "arguments": {"requesting_agent": "repo-liaison", "action": "write x/README"}}},
        refs,
    )


def _seed_offline_waiver(team: Path) -> None:
    refs = team / "references"
    refs.mkdir(parents=True, exist_ok=True)
    fields = ["waiver-M6R4", "security-intel-freshness", "2099-01-01T00:00:00Z", "20", "0",
              "security", "M6R4-1", "TEST", "verified"]
    sig = hmac.new(_WAIVER_KEY.encode(), "|".join(fields).encode(), hashlib.sha256).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        "2026-05-03T00:00:00Z," + ",".join(fields) + f",{sig}\n",
        encoding="utf-8",
    )


def _render(proj: Path, *extra: str) -> int:
    return build_team.main([
        "--description", str(BRIEF), "--project", str(proj), "--framework", "goose",
        "--yes", "--no-scan", "--security-offline", *extra,
    ])


def test_rerender_leaves_hand_appended_rows_byte_identical(tmp_path, monkeypatch) -> None:
    if not BRIEF.is_file():
        pytest.skip("research-project brief not found")
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", _WAIVER_KEY)
    proj = tmp_path / "proj"
    proj.mkdir()
    team = proj / ".goose" / "recipes"
    _seed_offline_waiver(team)
    assert _render(proj) == 0

    log = team / "references" / "security-decisions.log.csv"
    assert log.is_file(), "a goose render emits the decisions log"
    with log.open("ab") as fh:
        fh.write(HAND_ROWS.encode("utf-8"))
    before = log.read_bytes()

    assert _render(proj, "--update", "--merge") == 0
    assert log.read_bytes() == before, "a re-render rewrote the security-decisions log"

    # The Goose team's own runtime writer appends; it does not re-render the rows above.
    resp = _request_clearance(_load_server(), team / "references")
    assert resp["result"]["isError"] is False
    after = log.read_bytes()
    assert after.startswith(before), "existing rows were rewritten (re-quoted) instead of appended to"
    added = after[len(before):].decode("utf-8")
    assert added.count("\n") == 1
    assert list(csv.reader(io.StringIO(added)))[0][3] == "REQUESTED"


def test_clearance_request_keeps_crlf_and_quoting(tmp_path) -> None:
    refs = tmp_path / "references"
    refs.mkdir()
    log = refs / "security-decisions.log.csv"
    body = ("timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\r\n"
            + HAND_ROWS.replace("\n", "\r\n"))
    log.write_bytes(body.encode("utf-8"))
    assert _request_clearance(_load_server(), refs)["result"]["isError"] is False
    after = log.read_bytes()
    assert after.startswith(body.encode("utf-8"))
    assert after.endswith(b"\r\n") and after[len(body):].count(b"\n") == 1


def test_clearance_request_supplies_a_missing_final_newline(tmp_path) -> None:
    refs = tmp_path / "references"
    refs.mkdir()
    log = refs / "security-decisions.log.csv"
    body = "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n" + HAND_ROWS.rstrip("\n")
    log.write_text(body, encoding="utf-8")
    assert _request_clearance(_load_server(), refs)["result"]["isError"] is False
    rows = list(csv.reader(log.open(encoding="utf-8", newline="")))
    assert log.read_text(encoding="utf-8").startswith(body + "\n")
    assert len(rows) == 4 and rows[3][3] == "REQUESTED"


def test_clearance_request_refuses_a_log_with_an_open_quote(tmp_path) -> None:
    refs = tmp_path / "references"
    refs.mkdir()
    log = refs / "security-decisions.log.csv"
    body = 'timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n2026-10-20,a,"open\n'
    log.write_text(body, encoding="utf-8")
    resp = _request_clearance(_load_server(), refs)
    assert resp["result"]["isError"] is True
    assert log.read_text(encoding="utf-8") == body
