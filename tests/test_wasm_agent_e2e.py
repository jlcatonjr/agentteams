"""Live end-to-end tests of the WASM agent host (WASM P05): the test guest under wasmtime + the stub provider.

Needs the dev wasmtime (``AGENTTEAMS_WASM_DEV_WASMTIME``) and the DEV-ONLY CPython WASI build
(``AGENTTEAMS_WASM_DEV_PYTHON``, else Pylance's bundled one); skips otherwise. Neither is the pinned build (D9), so
these prove the host's behaviour, not the pins. Seatbelt cannot nest inside this session, so guests run with
``os_confinement="none"`` and snapshot-enforced read-only mounts; the wrapped Stage 1 runs with an empty OS-profile
prefix (its profile is unit-tested in ``test_wasm_agent_launch.py``).

The R13 step-time measurement runs through a real ``wasm_pins.PinnedResolver`` over an install dir built from the
dev runtime, so every step pays Stage 0's prepare, the pre-exec re-hash and the interpreter start. Set
``AGENTTEAMS_WASM_R13_GATE=1`` to enforce the 2 s median gate (the merge gate is on wasmtime 36.0.20 + pinned CPython).
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

from agentteams import _sandbox_profiles as SP
from agentteams import wasm_exec as W
from agentteams import wasm_pins
from agentteams.wasm_agent import broker as B
from agentteams.wasm_agent import launch as L
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.host import AgentSpec
from agentteams.wasm_agent.transcript import verify_transcript
from tests._wasm_agent_helpers import GUEST_SHA, GUEST_SRC, text_reply, tool_reply
from tests._wasm_helpers import needs_wasmtime
from tests.test_wasm_agent_stage_split import _HOOK
from tests.test_wasm_guests_live import DevResolver, dev_python_dir, needs_dev_python

pytestmark = [needs_wasmtime, needs_dev_python]

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
    real = B.execute

    def spy(launch: Any, stdin: bytes, **kw: Any) -> Any:
        seen.append(list(launch.argv))
        return real(launch, stdin, **kw)
    monkeypatch.setattr(B, "execute", spy)
    return seen


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
    res = _run(root, scratch, script)
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
    assert "WASM host not OS-confined" in " ".join(res.notices)


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
    res = _run(root, scratch, [])
    assert res.status == "ok"
    results = {b["tool_use_id"]: b for b in seen[1][-1]["content"]}
    assert results["t1"]["content"] == "granted doc\n" and results["t1"]["is_error"] is False
    assert results["t2"]["is_error"] is True and "not granted" not in results["t2"]["content"]


def test_fuel_trap_in_run_code_is_an_error_result(tmp_path: Path) -> None:
    root, scratch = _project(tmp_path)
    res = _run(root, scratch, [tool_reply(("t1", "run_code", {"language": "python", "source": "while True: pass"})),
                               text_reply("k")],
               spec=_spec(root, scratch, limits=W.Limits(3_000_000_000, 64 << 20, 30)))
    tool = [r for r in _records(res) if r["kind"] == "host-tool"][0]
    assert tool["ok"] is False and tool["trap"] == "fuel"


def test_guest_sees_an_empty_environment(tmp_path: Path) -> None:
    root, scratch = _project(tmp_path)
    res = _run(root, scratch, [text_reply("GUEST:env")])
    assert res.reason == "guest:env-0"


def test_misbehaving_guest_fails_the_run(tmp_path: Path) -> None:
    root, scratch = _project(tmp_path)
    res = _run(root, scratch, [text_reply("GUEST:v2")])
    assert (res.status, res.reason) == ("failed", "protocol-version")


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
