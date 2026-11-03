"""Live end-to-end tests of the WASM agent host (WASM P05): the test guest under wasmtime + the stub provider.

Needs the dev wasmtime (``AGENTTEAMS_WASM_DEV_WASMTIME``) and the DEV-ONLY CPython WASI build
(``AGENTTEAMS_WASM_DEV_PYTHON``, else Pylance's bundled one); skips otherwise. Neither is the pinned build (D9), so
these prove the host's behaviour, not the pins. Seatbelt cannot nest inside this session, so guests run with
``os_confinement="none"`` and snapshot-enforced read-only mounts; the wrapped Stage 1 runs with an empty OS-profile
prefix (its profile is unit-tested in ``test_wasm_agent_launch.py``).

The R13 step-time measurement runs through a real ``wasm_pins.PinnedResolver`` over an install dir built from the
dev runtime, so every step pays Stage 0's prepare, the pre-exec re-hash and the interpreter start. Set
``AGENTTEAMS_WASM_R13_GATE=1`` to enforce the 2 s median gate (the merge gate is on wasmtime 36.0.20 + pinned CPython).

The P17 section at the end is ARCH-B end to end across plans (P01 brief -> P08 render -> ``--run-wasm-agent`` -> P05
host -> P09 delegation): see its own header. It has a native, runtime-free variant (the pinned guest run as a native
process, ``tests/_wasm_delegation_helpers.GuestPreparer``) and a live one; a live-provider run is opt-in only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import types
from pathlib import Path
from typing import Any

import pytest

import build_team
from agentteams import _sandbox_profiles as SP
from agentteams import model_providers as MP
from agentteams import wasm_exec as W
from agentteams import wasm_pins
from agentteams.cli import wasm_agent_commands as WC
from agentteams.wasm_agent import launch as L
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.host import AgentSpec
from agentteams.wasm_agent.transcript import verify_transcript
from tests._wasm_agent_helpers import GUEST_SHA, GUEST_SRC, text_reply, tool_reply
from tests._wasm_delegation_helpers import GuestPreparer, native_guests
from tests._wasm_delegation_helpers import Resolver as NativeResolver
from tests._wasm_framework_helpers import project as fw_project
from tests._wasm_framework_helpers import render, write_brief
from tests._wasm_helpers import needs_wasmtime
from tests.test_wasm_agent_stage_split import _HOOK
from tests.test_wasm_guests_live import DevResolver, dev_python_dir, needs_dev_python


__all__ = ["native_guests"]   # a fixture, used by the P17 section

AGENT = "primary-producer"
LIMITS = W.Limits(fuel=7_000_000_000, max_memory_bytes=64 << 20, timeout_s=30)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("granted doc\n")
    (root / "secret").mkdir()
    (root / "secret" / "s.txt").write_text("not granted\n")
    return root, root / ".agentteams" / "wasm" / "scratch" / AGENT


def _spec(root: Path, scratch: Path, tools: frozenset[str] = frozenset({"read_file", "write_file", "run_code"}),
          limits: W.Limits = LIMITS) -> AgentSpec:
    grant = W.Grant((W.Mount(str(root / "docs"), "/project/docs", "ro"), W.Mount(str(scratch), "/scratch", "rw")),
                    limits)
    return AgentSpec(AGENT, "You help.", tools, frozenset(), grant, "stub", "test-model", 512, "0" * 64)


def _run(root: Path, scratch: Path, script: list, *, resolver: Any = None, spec: AgentSpec | None = None,
         **kw: Any) -> Any:
    return L.stage0(spec or _spec(root, scratch), "Work on the docs.", project_root=root, budget=Budget(),
                    guest_source=GUEST_SRC, guest_sha256=GUEST_SHA, stub_script=script,
                    resolver=resolver or DevResolver(), **kw)


def _records(res: Any) -> list[dict[str, Any]]:
    return [json.loads(x) for x in Path(res.transcript_path).read_text().splitlines()]


@pytest.fixture
def argv_spy(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []
    real = W.execute_launch

    def spy(launch: Any, stdin: bytes, **kw: Any) -> Any:
        seen.append(list(launch.argv))
        return real(launch, stdin, **kw)
    monkeypatch.setattr(W, "execute_launch", spy)
    return seen


@needs_wasmtime
@needs_dev_python
def test_full_run_in_process(tmp_path: Path, argv_spy: list) -> None:
    root, scratch = _project(tmp_path)
    script = [
        tool_reply(("t1", "read_file", {"path": "/project/docs/a.md"}),
                   ("t2", "read_file", {"path": "/project/secret/s.txt"}),
                   ("t3", "write_file", {"path": "/scratch/out.txt", "content": "from the guest"}),
                   ("t4", "run_code", {"language": "python", "source":
                    "import os\ntry:\n    open('/project/docs/a.md', 'w').write('x')\n    print('WROTE')\n"
                    "except OSError as e:\n    print('DENIED', type(e).__name__)\nprint(6 * 7)"})),
        tool_reply(("t5", "read_file", {"path": "/scratch/out.txt"})),
        text_reply("finished"),
    ]
    # host_confine=off keeps Stage 1 in this process, where the spy reaches it; each guest still nests where it can
    res = _run(root, scratch, script, host_confine="off")
    assert (res.status, res.output) == ("ok", "finished"), res
    assert (root / "docs" / "a.md").read_text() == "granted doc\n"          # ro: host file unchanged
    assert (scratch / "out.txt").read_text() == "from the guest"            # rw scratch persisted
    rep = verify_transcript(res.transcript_path)
    assert rep.ok and rep.complete
    recs = _records(res)
    steps = [r for r in recs if r["kind"] == "guest-step"]
    assert steps and all(r["os_confinement"] in ("none", "nested") for r in steps) and all(not r["error"] for r in steps)
    tool = [r for r in recs if r["kind"] == "host-tool"]
    assert len(tool) == 1 and tool[0]["ok"] is True and tool[0]["os_confinement"] in ("none", "nested")
    replies = [r for r in recs if r["kind"] == "model-reply"]
    assert len(replies) == 3
    assert all(a[a.index("-C") + 1] == "cache=n" for a in argv_spy)          # no compile cache (R6)
    assert len(argv_spy) == len(steps) + 1
    assert not (root / ".agentteams" / "wasm" / "exec").exists() or not any(
        (root / ".agentteams" / "wasm" / "exec").iterdir())                 # exec dirs removed at release
    assert "WASM host not OS-confined: host_confine=off" in res.notices


@needs_wasmtime
@needs_dev_python
def test_in_guest_results_reach_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[dict]] = []

    def script(messages: list, system: Any, tools: Any) -> dict:
        seen.append(messages)
        if len(messages) == 1:
            return tool_reply(("t1", "read_file", {"path": "/project/docs/a.md"}),
                              ("t2", "read_file", {"path": "/project/secret/s.txt"}))
        return text_reply("ok")
    from agentteams import model_providers as MP
    monkeypatch.setattr(L, "build_provider", lambda *a, **k: MP.Provider(MP.PROVIDERS["stub"], None, "",
                                                                         MP.StubProvider(script)))
    root, scratch = _project(tmp_path)
    res = _run(root, scratch, [], host_confine="off")                       # in-process, so the patch reaches it
    assert res.status == "ok"
    results = {b["tool_use_id"]: b for b in seen[1][-1]["content"]}
    assert results["t1"]["content"] == "granted doc\n" and results["t1"]["is_error"] is False
    assert results["t2"]["is_error"] is True and "not granted" not in results["t2"]["content"]


@needs_wasmtime
@needs_dev_python
def test_fuel_trap_in_run_code_is_an_error_result(tmp_path: Path) -> None:
    root, scratch = _project(tmp_path)
    res = _run(root, scratch, [tool_reply(("t1", "run_code", {"language": "python", "source": "while True: pass"})),
                               text_reply("k")],
               spec=_spec(root, scratch, limits=W.Limits(3_000_000_000, 64 << 20, 30)))
    tool = [r for r in _records(res) if r["kind"] == "host-tool"][0]
    assert tool["ok"] is False and tool["trap"] == "fuel"


@needs_wasmtime
@needs_dev_python
def test_guest_sees_an_empty_environment(tmp_path: Path) -> None:
    root, scratch = _project(tmp_path)
    res = _run(root, scratch, [text_reply("GUEST:env")])
    assert res.reason == "guest:env-0"


@needs_wasmtime
@needs_dev_python
def test_misbehaving_guest_fails_the_run(tmp_path: Path) -> None:
    root, scratch = _project(tmp_path)
    res = _run(root, scratch, [text_reply("GUEST:v2")])
    assert (res.status, res.reason) == ("failed", "protocol-version")


@needs_wasmtime
@needs_dev_python
def test_wrapped_stage1_with_a_real_guest_writes_nothing_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, scratch = _project(tmp_path)
    log = tmp_path / "audit.log"
    log.write_text("")
    agt = os.path.join(str(root), ".agentteams")
    monkeypatch.setattr(L, "_BOOT", _HOOK.format(root=agt, log=str(log), control="") + L._BOOT)
    monkeypatch.setattr(L, "host_profile_argv", lambda kind, **kw: [])
    fake = types.SimpleNamespace(**{k: v for k, v in vars(SP).items() if not k.startswith("__")})
    fake.available = lambda: "seatbelt"
    res = _run(root, scratch, [tool_reply(("t1", "write_file", {"path": "/scratch/w.txt", "content": "via wasm"}),
                                          ("t2", "run_code", {"language": "python", "source": "print(1 + 1)"})),
                               text_reply("wrapped done")], profiles=fake)
    assert (res.status, res.output) == ("ok", "wrapped done"), res
    assert (scratch / "w.txt").read_text() == "via wasm"                    # written by the wasmtime child
    lines = log.read_text().splitlines()
    assert lines[0] == "HOOKED" and not [x for x in lines if x.startswith("WRITE")], lines
    assert verify_transcript(res.transcript_path).complete


# --- R13: full step time (Stage-0 prepare + pre-exec re-hash + interpreter start) ---------------------------------

def _pinned_install(root: Path) -> Any:
    """Build ``<root>/.agentteams/wasm`` from the dev runtime and a pins mapping that matches it."""
    from tests._wasm_helpers import dev_wasmtime
    idir = root / ".agentteams" / "wasm"
    (idir / "wasmtime" / "0.0.1").mkdir(parents=True)
    shutil.copy2(str(dev_wasmtime()), idir / "wasmtime" / "0.0.1" / "wasmtime")
    shutil.copytree(dev_python_dir(), idir / "python" / "0.0.1")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()   # noqa: E731
    tree, count = wasm_pins.tree_digest(idir / "python" / "0.0.1" / "lib")
    pins = {"components": {
        "wasmtime": {"version": "0.0.1", "platforms": {wasm_pins.host_platform(): {
            "entry": "wasmtime", "files": {"wasmtime": {"sha256": sha(idir / "wasmtime" / "0.0.1" / "wasmtime")}}}}},
        "python": {"version": "0.0.1", "runtime_mounts": [{"guest": "/lib", "tree": "lib"}], "platforms": {"any": {
            "entry": "python.wasm", "files": {"python.wasm": {"sha256": sha(idir / "python" / "0.0.1" / "python.wasm")}},
            "tree": {"root": "lib", "sha256": tree, "count": count}}}}}}
    return wasm_pins.pinned_resolver(idir, pins)


@needs_wasmtime
@needs_dev_python
@pytest.mark.parametrize("in_place", [False, True], ids=["verified-copy", "in-place-R3prime"])
def test_r13_step_time(tmp_path: Path, in_place: bool, capsys: pytest.CaptureFixture[str]) -> None:
    root, scratch = _project(tmp_path)
    resolver = _pinned_install(root)
    script = [tool_reply((f"t{i}", "read_file", {"path": "/project/docs/a.md"})) for i in range(4)]
    res = _run(root, scratch, [*script, text_reply("timed")], resolver=resolver, control_plane_protected=in_place)
    assert res.status == "ok", res
    totals = [t["total_s"] for t in res.step_timings]
    prepare = [t["prepare_s"] for t in res.step_timings]
    median = statistics.median(totals)
    with capsys.disabled():
        print(f"\nR13 [{'in-place' if in_place else 'verified-copy'}] steps={len(totals)} median={median:.3f}s "
              f"min={min(totals):.3f}s max={max(totals):.3f}s median-prepare={statistics.median(prepare):.3f}s")
    assert len(totals) == 6
    limit = 2.0 if os.environ.get("AGENTTEAMS_WASM_R13_GATE") == "1" else 6.0
    assert median <= limit


# --- P17 ARCH-B: brief -> render -> --run-wasm-agent -> tool call -> delegation (D11) --------------------------------
#
# The example team (``examples/software-project/wasm-runtime.brief.json``) gives primary-producer docs ro + scratch and
# leaves the orchestrator compute-only with ``run_code`` + ``delegate``. One CLI run of the orchestrator makes one tool
# call (``run_code``), delegates once to primary-producer, and tries a delegation that carries a grant. Asserted: the
# tool result reaches the model; the child's declared docs mount is dropped (recorded) and it runs with no mounts and
# no file tools (D11: never wider than its delegator); the grant-carrying delegate is refused; the transcript tree
# verifies; the provider key the host holds never reaches a guest (stdin, environment) or a transcript.

KEY = "sk-ant-api03-" + "P17" * 12 + "-not-real"
_DELEGATE = {"agent": "primary-producer", "task": "Summarise the docs."}
_needs_host_in_process = pytest.mark.skipif(
    SP.available() is not None, reason="a nestable OS sandbox wraps Stage 1 in a subprocess, where the scripted "
                                        "provider and the spies cannot reach (operator: the pinned acceptance run)")


@pytest.fixture(scope="module")
def wasm_team(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = fw_project(tmp_path_factory.mktemp("archb"))
    assert render(root, write_brief(root, enforce_decision_signing=False)) == 0
    return root


def _scripted(seen: list[tuple[list[str], list[Any]]]) -> Any:
    """Parent (offered ``delegate``) and child replies, in order; records every request."""
    parent = [tool_reply(("t1", "run_code", {"language": "python", "source": "print(6*7)"})),
              tool_reply(("d1", "delegate", _DELEGATE)),
              tool_reply(("d2", "delegate", {**_DELEGATE, "grant": {"read_paths": ["src"], "scratch": True}})),
              text_reply("root done")]
    child = [tool_reply(("c1", "read_file", {"path": "/project/docs/a.md"})), text_reply("child done")]

    def script(messages: list, system: Any, tools: Any) -> dict:
        names = sorted(t.name for t in tools or [])
        seen.append((names, json.loads(json.dumps(messages))))
        queue = parent if "delegate" in names else child
        return queue.pop(0) if queue else text_reply("idle")
    return script


def _results(messages: list[Any]) -> dict[str, dict[str, Any]]:
    return {b["tool_use_id"]: b for m in messages if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "tool_result"}


def _arch_b(root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], resolver: Any
            ) -> tuple[dict[str, Any], list, list, list[str]]:
    seen: list[tuple[list[str], list[Any]]] = []
    keys: list[str] = []
    guest_io: list[tuple[dict[str, str], bytes]] = []

    def build(name: str, key: str, **kw: Any) -> MP.Provider:   # the host holds the real key; replies are scripted
        keys.append(key)
        return MP.Provider(MP.PROVIDERS["stub"], None, "", MP.StubProvider(_scripted(seen)))

    real = W.execute_launch

    def spy(launch: Any, stdin: bytes, **kw: Any) -> Any:
        guest_io.append((dict(launch.env), stdin))
        return real(launch, stdin, **kw)

    monkeypatch.setattr(WC, "runtime_resolver", lambda r: resolver)
    monkeypatch.setattr(L, "build_provider", build)
    monkeypatch.setattr(W, "execute_launch", spy)
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("AGENTTEAMS_WASM_MODEL_PRIMARY", "p17-model")
    monkeypatch.setenv("AGENTTEAMS_WASM_MODEL_CHEAP", "p17-model")
    capsys.readouterr()
    rc = build_team.main(["--run-wasm-agent", "orchestrator", "--wasm-task", "Coordinate.", "--wasm-provider",
                          "anthropic", "--project", str(root), "--json"])
    out = capsys.readouterr().out
    assert rc == 0, out[-2000:]
    data = json.loads(out.split("\n", 1)[1])
    return data, seen, guest_io, keys


def _check_arch_b(root: Path, data: dict[str, Any], seen: list, guest_io: list, keys: list[str],
                  capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    run = data["run"]
    assert (run["status"], run["output"]) == ("ok", "root done"), run
    parent_final = [m for names, m in seen if "delegate" in names][-1]
    got = _results(parent_final)
    assert not got["t1"]["is_error"] and "42" in got["t1"]["content"]                       # one tool call
    assert not got["d1"]["is_error"] and got["d1"]["content"].endswith("child done")         # one delegation
    assert got["d1"]["content"].startswith("[output of sub-agent primary-producer (run ")    # labelled data (C-4)
    assert got["d2"]["is_error"] and "delegate takes {agent, task}" in got["d2"]["content"]  # a grant is refused
    child_requests = [(names, m) for names, m in seen if "delegate" not in names]
    assert child_requests and all(names == [] for names, _ in child_requests)  # no file tools without a mount
    assert _results(child_requests[-1][1])["c1"]["is_error"]

    recs = [json.loads(x) for x in Path(run["transcript_path"]).read_text().splitlines()]
    starts = [r for r in recs if r["kind"] == "delegate-start"]
    assert len(starts) == 1 and starts[0]["agent"] == "primary-producer" and starts[0]["tools"] == []
    assert any(d.startswith("/project/docs: outside every delegator mount") for d in starts[0]["dropped"])
    assert any(d.startswith("/scratch: the delegator has no /scratch") for d in starts[0]["dropped"])
    assert build_team.main(["--verify-wasm-run", run["run_id"], "--tree", "--project", str(root)]) == 0
    assert "verified; 2 run(s)" in capsys.readouterr().out

    assert keys and set(keys) == {KEY}                    # the host loaded the key from its environment ...
    assert guest_io and all(env in ({}, {"SYSTEMROOT": env.get("SYSTEMROOT", "")}) for env, _ in guest_io)
    assert not any(KEY.encode() in stdin or KEY in json.dumps(env) for env, stdin in guest_io)   # ... never a guest
    for path in (root / ".agentteams" / "wasm-runs").rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path
    return starts


@_needs_host_in_process
def test_arch_b_native_render_run_tool_call_and_delegation(wasm_team: Path, native_guests: list,
                                                           monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    data, seen, guest_io, keys = _arch_b(wasm_team, monkeypatch, capsys, NativeResolver())
    _check_arch_b(wasm_team, data, seen, guest_io, keys, capsys)
    root_grant, child_grant = native_guests[0].grant, native_guests[1].grant
    assert root_grant.mounts == () and child_grant.mounts == ()          # D11: the child is not wider
    assert all(isinstance(g, GuestPreparer) for g in native_guests)


@needs_wasmtime
@needs_dev_python
@_needs_host_in_process
def test_arch_b_live_render_run_tool_call_and_delegation(wasm_team: Path, monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    data, seen, guest_io, keys = _arch_b(wasm_team, monkeypatch, capsys, DevResolver())
    _check_arch_b(wasm_team, data, seen, guest_io, keys, capsys)
    assert data["guest_os_confinement"] == ["none"]                       # said, never claimed as nested here


@pytest.mark.skipif(not os.environ.get("AGENTTEAMS_E2E_PROVIDER"),
                    reason="live provider is opt-in: set AGENTTEAMS_E2E_PROVIDER (+ its key and "
                           "AGENTTEAMS_WASM_MODEL_PRIMARY); never in default CI")
@needs_wasmtime
@needs_dev_python
def test_arch_b_live_provider_opt_in(wasm_team: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(WC, "runtime_resolver", lambda r: DevResolver())
    rc = build_team.main(["--run-wasm-agent", "orchestrator", "--wasm-task", "Reply with the word ready.",
                          "--wasm-provider", os.environ["AGENTTEAMS_E2E_PROVIDER"], "--project", str(wasm_team),
                          "--json"])
    out = capsys.readouterr().out
    assert rc == 0, out[-2000:]
    run = json.loads(out.split("\n", 1)[1])["run"]
    assert run["status"] == "ok"
    assert build_team.main(["--verify-wasm-run", run["run_id"], "--tree", "--project", str(wasm_team)]) == 0
