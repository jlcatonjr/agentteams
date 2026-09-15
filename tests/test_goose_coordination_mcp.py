"""Offline unit tests for scripts/goose-coordination-mcp.py.

The coordination MCP server ships in ``scripts/`` (not an importable package), so it is
loaded via importlib exactly like tests/test_goose_route_proxy.py loads the route proxy.
All tests are pure/offline: they drive the JSON-RPC handlers directly against a tmp
references dir — no sockets, no network, no goose binary.
"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

_SERVER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "goose-coordination-mcp.py"


def _load():
    spec = importlib.util.spec_from_file_location("goose_coordination_mcp", _SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


@pytest.fixture
def refs(tmp_path):
    d = tmp_path / "references"
    d.mkdir()
    return d


# --- protocol handshake -----------------------------------------------------------------

def test_initialize_reports_server_info(mod, refs):
    resp = mod.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, refs)
    assert resp["result"]["serverInfo"]["name"] == "agentteams_coordination"
    assert "tools" in resp["result"]["capabilities"]


def test_initialized_notification_gets_no_response(mod, refs):
    assert mod.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}, refs) is None


def test_tools_list_exposes_the_four_tools(mod, refs):
    resp = mod.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, refs)
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {
        "read_adjacent_registry",
        "file_coordination_request",
        "append_coordination_log",
        "request_security_clearance",
    }


def test_unknown_method_is_jsonrpc_error(mod, refs):
    resp = mod.handle_request({"jsonrpc": "2.0", "id": 3, "method": "no/such"}, refs)
    assert resp["error"]["code"] == -32601


# --- tools/call: record-keeping writes --------------------------------------------------

def _call(mod, refs, name, arguments):
    return mod.handle_request(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}},
        refs,
    )


def test_append_coordination_log_writes_valid_row(mod, refs):
    resp = _call(mod, refs, "append_coordination_log",
                 {"adjacent_repo": "vk-api-utils", "direction": "outbound", "outcome": "awaiting"})
    assert resp["result"]["isError"] is False
    csv_path = refs / "adjacent-repos-coordination-log.csv"
    rows = list(csv.reader(csv_path.open(encoding="utf-8")))
    assert rows[0] == ["date", "adjacent_repo", "direction", "outcome"]
    assert rows[1][1:] == ["vk-api-utils", "outbound", "awaiting"]


def test_append_coordination_log_rejects_bad_direction(mod, refs):
    resp = _call(mod, refs, "append_coordination_log",
                 {"adjacent_repo": "x", "direction": "sideways"})
    assert resp["result"]["isError"] is True


def test_file_coordination_request_writes_artifact(mod, refs):
    resp = _call(mod, refs, "file_coordination_request",
                 {"target_repo": "vk-services-local", "task": "rotate the gate config"})
    assert resp["result"]["isError"] is False
    reqs = list((refs / "cross-orchestrator-requests").glob("*.md"))
    assert len(reqs) == 1
    body = reqs[0].read_text(encoding="utf-8")
    assert "Coordination Request" in body and "vk-services-local" in body
    assert "does not execute or authorize" in body  # honest file-based framing


def test_request_security_clearance_records_request_not_grant(mod, refs):
    resp = _call(mod, refs, "request_security_clearance",
                 {"requesting_agent": "repo-liaison", "action": "write vk-api-utils/README"})
    assert resp["result"]["isError"] is False
    rows = list(csv.reader((refs / "security-decisions.log.csv").open(encoding="utf-8")))
    assert rows[0] == ["timestamp", "requesting_agent", "action_reviewed",
                       "verdict", "conditions", "conditions_verified"]
    # It RECORDS a request; it must NOT self-grant.
    assert rows[1][3] == "REQUESTED"
    assert rows[1][5] == "no"


def test_read_adjacent_registry_reads_when_present(mod, refs):
    (refs / "adjacent-repos.md").write_text("# Adjacent\n- vk-api-utils\n", encoding="utf-8")
    resp = _call(mod, refs, "read_adjacent_registry", {})
    assert "vk-api-utils" in resp["result"]["content"][0]["text"]


def test_unknown_tool_is_error(mod, refs):
    resp = _call(mod, refs, "definitely_not_a_tool", {})
    assert resp["result"]["isError"] is True


# --- safety: path containment + no autonomous grant/execute -----------------------------

def test_append_refuses_target_escaping_refs_dir(mod, refs):
    with pytest.raises(ValueError):
        mod._append_csv_row(refs, "../escape.csv", mod.COORD_LOG_HEADERS,
                            {"date": "d", "adjacent_repo": "a", "direction": "outbound", "outcome": "o"})


def test_no_tool_named_execute_or_grant(mod, refs):
    # Guards the @security HALT boundary: this file-based server must never expose an
    # autonomous execute/grant surface. Adding one requires the separate, re-reviewed design.
    names = set(mod._TOOLS)
    assert not any(k in n for n in names for k in ("execute", "grant", "run", "authorize", "approve"))
