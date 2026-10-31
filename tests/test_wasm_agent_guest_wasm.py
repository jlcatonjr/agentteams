"""Live tests of the production WASM agent guest (WASM P07): host + pinned guest program + stub provider.

Stage 0 runs the real ``agentteams/data/wasm_agent_guest.py`` (loaded through its pin, the default of ``stage0``) under
the dev wasmtime (``AGENTTEAMS_WASM_DEV_WASMTIME``) and the DEV-ONLY CPython WASI build (``AGENTTEAMS_WASM_DEV_PYTHON``,
else Pylance's bundled one); skips when either is absent. Neither is the pinned build (D9): these runs are development
evidence, and acceptance re-runs them on wasmtime 36.0.20 with the pinned CPython (operator command in
``PENDING-operator-commands.md``). Seatbelt cannot nest inside this session, so guests run un-nested
(``os_confinement="none"``) with snapshot-enforced read-only mounts.

Deny probes (plan §6): P-1 compute-only grant, P-2 empty environment, P-3 no sockets, P-4 symlink escape, P-5
read-only project mount, P-7 instruction-file names written past the soft check. P-6 (regex bomb) does not apply:
the merged host schema makes ``search_files`` a literal-substring search; fuel traps are covered by P05's tests.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from agentteams import model_providers as MP
from agentteams import wasm_exec as W
from agentteams.wasm_agent import launch as L
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.guest_source import GUEST_SHA256
from agentteams.wasm_agent.host import AgentSpec
from agentteams.wasm_agent.transcript import verify_transcript
from tests._wasm_agent_helpers import no_network, text_reply, tool_reply
from tests._wasm_helpers import needs_wasmtime
from tests.test_wasm_guests_live import DevResolver, needs_dev_python

pytestmark = [needs_wasmtime, needs_dev_python]
__all__ = ["no_network"]

AGENT = "primary-producer"
LIMITS = W.Limits(fuel=7_000_000_000, max_memory_bytes=64 << 20, timeout_s=30)
ALL = frozenset({"read_file", "list_dir", "search_files", "write_file", "run_code"})
SECRET = "not granted: s3cr3t-marker"


def _project(tmp_path: Path) -> tuple[Path, Path]:
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("granted doc\nwith a needle\n")
    (root / "secret").mkdir()
    (root / "secret" / "s.txt").write_text(SECRET + "\n")
    return root, root / ".agentteams" / "wasm" / "scratch" / AGENT


def _spec(root: Path, scratch: Path | None, tools: frozenset[str] = ALL, mounts: bool = True) -> AgentSpec:
    grant_mounts = ((W.Mount(str(root / "docs"), "/project/docs", "ro"), W.Mount(str(scratch), "/scratch", "rw"))
                    if mounts else ())
    return AgentSpec(AGENT, "You help.", tools, frozenset(), W.Grant(grant_mounts, LIMITS), "stub", "test-model", 512,
                     "0" * 64)


def _run(root: Path, spec: AgentSpec, replies: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
         ) -> tuple[Any, list[list[dict[str, Any]]]]:
    """Run Stage 0 in-process with the pinned guest; return the result and every message list the model saw."""
    seen: list[list[dict[str, Any]]] = []

    def script(messages: list, system: Any, tools: Any) -> dict:
        seen.append(json.loads(json.dumps(messages)))
        return replies[min(len(seen), len(replies)) - 1]
    monkeypatch.setattr(L, "build_provider", lambda *a, **k: MP.Provider(MP.PROVIDERS["stub"], None, "",
                                                                         MP.StubProvider(script)))
    res = L.stage0(spec, "Work on the docs.", project_root=root, budget=Budget(), stub_script=[],
                   resolver=DevResolver())
    return res, seen


def _records(res: Any) -> list[dict[str, Any]]:
    return [json.loads(x) for x in Path(res.transcript_path).read_text().splitlines()]


def _results(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {b["tool_use_id"]: b for b in messages[-1]["content"] if b.get("type") == "tool_result"}


def _probe_json(block: dict[str, Any]) -> dict[str, Any]:
    out = block["content"].split("--- stdout ---\n", 1)[1].split("\n--- stderr ---", 1)[0]
    return json.loads(out.strip().splitlines()[-1])


def test_end_to_end_one_model_turn_one_tool_call_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    root, scratch = _project(tmp_path)
    res, seen = _run(root, _spec(root, scratch), [tool_reply(("t1", "read_file", {"path": "/project/docs/a.md"})),
                                                  text_reply("finished")], monkeypatch)
    assert (res.status, res.reason, res.output) == ("ok", "done", "finished"), res
    assert len(seen) == 2
    assert _results(seen[1])["t1"] == {"type": "tool_result", "tool_use_id": "t1",
                                       "content": "granted doc\nwith a needle\n", "is_error": False}
    recs = _records(res)
    steps = [r for r in recs if r["kind"] == "guest-step"]
    assert [s["op"] for s in steps] == ["model", "model", "done"] and not any(s["error"] for s in steps)
    assert all(s["os_confinement"] in ("none", "nested") for s in steps)
    start = [r for r in recs if r["kind"] == "run-start"][0]
    assert start["tool_spec_sha256"]
    rep = verify_transcript(res.transcript_path)
    assert rep.ok and rep.complete
    assert hashlib.sha256(L.load_guest_source()[0]).hexdigest() == GUEST_SHA256
    totals = sorted(t["total_s"] for t in res.step_timings)
    with capsys.disabled():
        print(f"\nP07 guest steps={len(totals)} median={totals[len(totals) // 2]:.3f}s max={totals[-1]:.3f}s")


_PROBE = r"""
import json, os
out = {}
def attempt(name, fn):
    try:
        fn()
        out[name] = "ALLOWED"
    except BaseException as exc:
        out[name] = "denied:" + type(exc).__name__
attempt("etc_passwd", lambda: open("/etc/passwd").read())
attempt("list_root_secret", lambda: open("/project/secret/s.txt").read())
attempt("write_project", lambda: open("/project/docs/a.md", "w").write("pwned"))
def sock():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", 9))
attempt("socket", sock)
out["env"] = len(os.environ)
for name in ("CLAUDE.md", "x.agent.md"):
    with open("/scratch/" + name, "w") as fh:
        fh.write("obey")
out["scratch"] = sorted(os.listdir("/scratch"))
print(json.dumps(out))
"""


def test_deny_probes_through_run_code_and_the_in_guest_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, scratch = _project(tmp_path)
    before = hashlib.sha256((root / "docs" / "a.md").read_bytes()).hexdigest()
    uses = tool_reply(("t1", "read_file", {"path": "/project/secret/s.txt"}),                  # outside the grant
                      ("t2", "list_dir", {"path": "/"}),
                      ("t3", "write_file", {"path": "/scratch/CLAUDE.md", "content": "obey"}),  # R8 soft layer
                      ("t4", "write_file", {"path": "/scratch/notes.txt", "content": "kept"}),
                      ("t5", "search_files", {"path": "/project/docs", "pattern": "needle"}),
                      ("t6", "read_file", {"path": "/src/main.py"}),
                      ("t7", "run_code", {"language": "python", "source": _PROBE}))
    res, seen = _run(root, _spec(root, scratch), [uses, text_reply("probed")], monkeypatch)
    assert (res.status, res.output) == ("ok", "probed"), res
    got = _results(seen[1])
    assert got["t1"]["is_error"] and SECRET not in got["t1"]["content"]
    assert got["t2"]["is_error"] and got["t6"]["is_error"]
    assert got["t3"]["is_error"] and "instruction-file" in got["t3"]["content"]
    assert not got["t4"]["is_error"] and (scratch / "notes.txt").read_text() == "kept"
    assert got["t5"]["content"] == "/project/docs/a.md:2:with a needle"
    probe = _probe_json(got["t7"])
    assert probe["etc_passwd"].startswith("denied") and probe["list_root_secret"].startswith("denied")      # P-1
    assert probe["env"] == 0                                                                                 # P-2
    assert probe["socket"].startswith("denied")                                                              # P-3
    assert hashlib.sha256((root / "docs" / "a.md").read_bytes()).hexdigest() == before                       # P-5
    # P-7: past the soft check, the names land only in the agent's scratch dir, where copy-out refuses them (R8).
    assert {"CLAUDE.md", "x.agent.md"} <= set(probe["scratch"])
    assert sorted(p.name for p in scratch.iterdir() if W.copy_out_refused(p.name)) == ["CLAUDE.md", "x.agent.md"]
    assert not (root / "CLAUDE.md").exists() and not (root / "docs" / "CLAUDE.md").exists()
    assert not any(SECRET in json.dumps(m) for m in seen)


def test_compute_only_grant_sees_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _project(tmp_path)
    probe = "import os, json\nout = {}\nfor p in ('/', '/project', '/scratch', '/etc'):\n    try:\n" \
            "        out[p] = sorted(os.listdir(p))\n    except OSError as e:\n        out[p] = 'denied:' + type(e).__name__\n" \
            "print(json.dumps(out))"
    uses = tool_reply(("t1", "read_file", {"path": "/project/docs/a.md"}),
                      ("t2", "run_code", {"language": "python", "source": probe}))
    res, seen = _run(root, _spec(root, None, mounts=False), [uses, text_reply("k")], monkeypatch)
    assert res.status == "ok", res
    got = _results(seen[1])
    assert got["t1"]["is_error"]                                                                      # P-1 (soft)
    listing = _probe_json(got["t2"])
    for path in ("/project", "/scratch", "/etc"):
        assert str(listing[path]).startswith("denied"), listing                                       # P-1 (hard)
    assert str(listing["/"]).startswith("denied") or listing["/"] == [], listing      # no preopen reaches the host


def test_symlink_out_of_a_read_mount_is_not_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, scratch = _project(tmp_path)
    os.symlink(root / "secret" / "s.txt", root / "docs" / "abs-link")
    os.symlink("../secret/s.txt", root / "docs" / "rel-link")
    src = "for p in ('/project/docs/abs-link', '/project/docs/rel-link'):\n    try:\n        print(open(p).read())\n" \
          "    except OSError as e:\n        print('denied', type(e).__name__)"
    uses = tool_reply(("t1", "read_file", {"path": "/project/docs/abs-link"}),
                      ("t2", "read_file", {"path": "/project/docs/rel-link"}),
                      ("t3", "run_code", {"language": "python", "source": src}))
    res, seen = _run(root, _spec(root, scratch), [uses, text_reply("k")], monkeypatch)
    # P-4: either the snapshot refuses the symlinked mount outright (R7), or every read of the link fails.
    assert res.status in ("ok", "refused"), res
    assert not any(SECRET in json.dumps(m) for m in seen)
    if res.status == "ok":
        got = _results(seen[1])
        assert got["t1"]["is_error"] and got["t2"]["is_error"]
        assert got["t3"]["content"].count("denied") == 2, got["t3"]
    else:
        assert res.reason == "prepare-refused" and seen == []
