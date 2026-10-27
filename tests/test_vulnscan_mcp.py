"""agentteams.vulnscan.mcp robustness: list_findings ordering/truncation (D8), stdio JSON-RPC errors (E21), atomic spool."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import mcp
from agentteams.vulnscan.mcp import Server, ToolError


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    d = {"run": tmp_path / "run", "requests": tmp_path / "requests", "results": tmp_path / "results"}
    for p in d.values():
        p.mkdir()
    (d["run"] / "summary.md").write_text("# report\n")
    (d["run"] / "summary.json").write_text(json.dumps({"findings": [
        {"kind": "package", "target": "svc", "name": "PyJWT", "version": "2.3.0", "severity": 4, "fix": "2.15.0"}]}))
    return d


def _server(d: dict[str, Path]) -> Server:
    return Server(d["run"], d["requests"], d["results"])


# ---------------------------------------------------------------- D8

def test_list_findings_sorted_with_total_and_truncation_flag(dirs: dict[str, Path]) -> None:
    findings: list[dict[str, Any]] = [{"kind": "package", "target": f"t{i % 7}", "name": f"p{i}", "version": "1",
                                       "severity": [1, 2, 3, 4][i % 4], "malicious": False} for i in range(598)]
    findings.insert(300, {"kind": "package", "target": "t1", "name": "evil", "version": "6.6.6", "severity": 4,
                          "malicious": True})
    findings.insert(450, {"kind": "package", "target": "t2", "name": "worst", "version": "1", "severity": 4})
    (dirs["run"] / "summary.json").write_text(json.dumps({"findings": findings}))
    out = _server(dirs).call("list_findings", {})
    assert out["total"] == 600 and out["truncated"] is True and len(out["findings"]) == mcp.MAX_FINDINGS == 500
    rows = out["findings"]
    assert rows[0]["name"] == "evil"                                          # malicious first
    sev = [r["severity"] for r in rows]
    assert sev == sorted(sev, reverse=True) and sev.count(4) == 151 and sev[-1] == 1   # critical first; lows cut
    small = _server(dirs).call("list_findings", {"min_severity": "critical"})
    assert small["truncated"] is False and small["total"] == len(small["findings"]) == 151


def test_list_findings_reads_a_summary_with_a_schema_version(dirs: dict[str, Path]) -> None:
    """Item 23: the versioned summary.json is read as before."""
    data = json.loads((dirs["run"] / "summary.json").read_text())
    (dirs["run"] / "summary.json").write_text(json.dumps({"schema_version": 1, **data}))
    out = _server(dirs).call("list_findings", {})
    assert [r["name"] for r in out["findings"]] == ["PyJWT"] and out["total"] == 1


def test_list_findings_tolerates_malformed_rows(dirs: dict[str, Path]) -> None:
    (dirs["run"] / "summary.json").write_text(json.dumps({"findings": [
        {"name": "a", "severity": "high"}, "not-a-row", {"name": "b"}, {"name": "c", "severity": 3}]}))
    out = _server(dirs).call("list_findings", {"min_severity": "low"})
    assert [r["name"] for r in out["findings"]] == ["c"] and out["total"] == 1
    (dirs["run"] / "summary.json").write_text("[1, 2]")
    with pytest.raises(ToolError):
        _server(dirs).call("list_findings", {})


# ---------------------------------------------------------------- V20: size caps

def test_rationale_is_capped_and_long_targets_are_refused(dirs: dict[str, Path]) -> None:
    s = _server(dirs)
    q = s.call("request", {"op": "stage-fix", "target": "/r", "rationale": "w" * 5000})
    req = json.loads((dirs["requests"] / f"{q['id']}.json").read_text())
    assert req["rationale"] == "w" * 4000                                       # capped, not refused
    edge = "/" + "a" * 4095                                                     # exactly 4096: accepted
    assert s.call("request", {"op": "stage-fix", "target": edge, "rationale": "r"})["status"] == "queued"
    long = "/" + "a" * 4096
    with pytest.raises(ToolError, match="invalid target"):
        s.call("request", {"op": "stage-fix", "target": long, "rationale": "r"})
    with pytest.raises(ToolError, match="invalid target"):
        s.call("escalate", {"target": long, "kind": "ioc", "evidence": "e"})
    assert len(list(dirs["requests"].glob("*.json"))) == 2                      # nothing queued for the refusals


# ---------------------------------------------------------------- atomic spool write

def test_spool_write_leaves_no_temp_and_no_json_when_rename_fails(dirs: dict[str, Path],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    s = _server(dirs)
    s.call("request", {"op": "server-audit", "target": "web1", "rationale": "r"})
    assert [p.name for p in dirs["requests"].iterdir() if p.name.endswith(".tmp")] == []

    def boom(src: object, dst: object) -> None:
        raise OSError("crash between write and rename")

    monkeypatch.setattr(mcp.os, "replace", boom)
    with pytest.raises(OSError):
        s.call("request", {"op": "server-audit", "target": "web1", "rationale": "r"})
    names = sorted(p.name for p in dirs["requests"].iterdir())
    assert len(names) == 1 and names[0].endswith(".json")                       # only the first request
    assert not [n for n in names if n.startswith(".")]                          # no temp left behind


# ---------------------------------------------------------------- E21 stdio robustness

def _stdio(dirs: dict[str, Path], lines: list[str]) -> list[dict[str, Any]]:
    proc = subprocess.run([sys.executable, "-m", "agentteams.vulnscan.mcp", "--run-dir", str(dirs["run"]),
                           "--spool", str(dirs["requests"]), "--results", str(dirs["results"])],
                          input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return [json.loads(ln) for ln in proc.stdout.splitlines()]


def test_stdio_answers_malformed_input_and_keeps_serving(dirs: dict[str, Path]) -> None:
    def rpc(i: int, method: str, params: Any = None) -> str:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": i, "method": method}
        if params is not None:
            msg["params"] = params
        return json.dumps(msg)

    lines = ["this is not json",                                                    # parse error, id null
             rpc(1, "initialize", ["not", "an", "object"]),                         # params not an object
             rpc(2, "tools/call", "list_findings"),                                 # params a string
             rpc(3, "no/such/method", {}),
             rpc(4, "tools/call", {"name": "list_findings", "arguments": ["x"]}),   # arguments not an object
             rpc(5, "tools/call", {"name": "list_findings", "arguments": "x"}),
             '{"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "' + "x" * (5 * 1024 * 1024) + '"}}',
             "[1, 2, 3]",                                                            # not an object: ignored
             rpc(7, "ping"),
             rpc(8, "tools/call", {"name": "list_findings", "arguments": {}})]
    out = _stdio(dirs, lines)
    by_id = {o["id"]: o for o in out}
    assert out[0]["id"] is None and out[0]["error"]["code"] == -32700
    for i in (1, 2, 4, 5):
        assert by_id[i]["error"]["code"] == -32602, by_id[i]
    assert by_id[3]["error"]["code"] == -32601
    oversized = [o for o in out if o.get("error", {}).get("code") == -32600]
    assert len(oversized) == 1 and oversized[0]["id"] is None                  # answered without parsing
    assert by_id[7]["result"] == {}
    assert "PyJWT" in by_id[8]["result"]["content"][0]["text"]                  # still serving afterwards
    assert [o["id"] for o in out][-2:] == [7, 8]



# ---------------------------------------------------------------- no busy-polling; staged fixes (2026-10-27 run)

def test_a_pending_request_says_do_not_poll_and_repeated_polls_are_refused(dirs: dict[str, Path]) -> None:
    """The 2026-10-27 run polled one pending id 69 times until the token cap ended the run."""
    srv, rid = _server(dirs), "a" * 32
    for _ in range(mcp.MAX_PENDING_POLLS):
        out = srv.call("request_status", {"id": rid})
        assert out["status"] == "pending" and "Do not poll again" in out["note"]
    with pytest.raises(ToolError, match="polling refused"):
        srv.call("request_status", {"id": rid})
    (dirs["results"] / f"{rid}.json").write_text(json.dumps({"id": rid, "status": "done"}))
    assert srv.call("request_status", {"id": rid})["status"] == "done"   # a result is always returned
    assert srv.call("request_status", {"id": "b" * 32})["status"] == "pending"   # counted per id


def test_a_staged_repository_gets_open_pr_not_a_second_stage_fix(dirs: dict[str, Path]) -> None:
    (dirs["run"] / mcp.STAGED_FILE).write_text(json.dumps({"repos": ["/repos/app"]}))
    (dirs["run"] / "fixplan-app-1.md").write_text("# Fix plan (dry run)\n\nRepository (the `stage-fix` target): `/repos/app`\n")
    srv = _server(dirs)
    plans = srv.call("get_fix_plans", {})
    assert "Already staged: request open-pr" in plans and "- `/repos/app`" in plans
    with pytest.raises(ToolError, match="already staged"):
        srv.call("request", {"op": "stage-fix", "target": "/repos/app/", "rationale": "GHSA-x high"})
    assert srv.call("request", {"op": "open-pr", "target": "/repos/app", "rationale": "GHSA-x high"})
    assert srv.call("request", {"op": "stage-fix", "target": "/repos/other", "rationale": "GHSA-y high"})
    (dirs["run"] / mcp.STAGED_FILE).write_text("not json")
    assert srv.call("request", {"op": "stage-fix", "target": "/repos/app", "rationale": "r"})   # bad index: no hold
