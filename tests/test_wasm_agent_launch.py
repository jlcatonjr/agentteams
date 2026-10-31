"""Tests for agentteams/wasm_agent/launch.py + stage1.py (WASM P05 §4.9): Stage 0, key custody, the Stage 1 pipe.

Seatbelt cannot nest inside this session's sandbox, so the *wrapped* path runs Stage 1 as a real subprocess with an
empty OS-sandbox prefix (``host_profile_argv`` monkeypatched): every pipe, the bootstrap, the journal and the broker
are real; only the profile is absent. The profile text itself is unit-tested. Guests here are native Python stand-ins
returned by a test-only preparer (``NativePreparer``); the live WASM variant is in ``test_wasm_agent_e2e.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import wasm_exec as W
from agentteams.wasm_agent import launch as L
from agentteams.wasm_agent import protocol as P
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.transcript import verify_transcript
from tests._wasm_agent_helpers import GUEST_SHA, GUEST_SRC, KEY, project, spec, text_reply

#: A native stand-in guest: start -> model, a text reply -> done ok, "ENV" -> report the environment size.
_NATIVE_GUEST = r"""
import json, os, sys
f = json.loads(sys.stdin.readline()); ev = f["event"]
if ev["kind"] == "start":
    op = {"v": 1, "op": "model", "tool_results": [], "continue": False, "carry": None}
elif ev["kind"] == "model_reply" and "".join(b.get("text", "") for b in ev["content"]) == "ENV":
    extra = set(os.environ) - {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}   # added by macOS / CPython's C-locale coercion
    op = {"v": 1, "op": "done", "status": "failed", "reason": "env-%d" % len(extra)}
else:
    op = {"v": 1, "op": "done", "status": "ok"}
print(json.dumps(op))
"""


class FakeResolver:
    def resolve(self, component: str) -> Path:
        return Path(sys.executable)


class NativePreparer:
    """Test-only StepPreparer: every prepare is a native python running ``_NATIVE_GUEST``."""

    last: NativePreparer | None = None

    def __init__(self, **kw: Any) -> None:
        if hashlib.sha256(kw["guest_source"]).hexdigest() != kw["guest_sha256"]:
            from agentteams.wasm_agent.broker import BrokerError
            raise BrokerError("the agent guest source does not match its pinned sha256")
        self.kw, self.os_confine, self.executor = kw, kw["os_confine"], W
        self.prepared: list[str] = []
        self.released: list[str] = []
        NativePreparer.last = self

    def prepare(self, purpose: str, *, language: str | None = None, source: str | None = None) -> tuple:
        self.prepared.append(purpose)
        launch = W.Launch(argv=[sys.executable, "-I", "-c", _NATIVE_GUEST], env={}, cwd="/", run_dir=None,
                          cleanup=lambda: None, os_confinement="none", os_reason="native-test", read_only_mode="none",
                          timeout_s=30, module_sha256="0" * 64)
        return f"t{len(self.prepared)}", launch, 0.0

    def release(self, ticket: str) -> None:
        self.released.append(ticket)

    def release_all(self) -> None:
        pass


def _fake_profiles(kind: str | None) -> types.SimpleNamespace:
    fake = types.SimpleNamespace(**{k: v for k, v in vars(SP).items() if not k.startswith("__")})
    fake.available = lambda: kind
    return fake


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(L, "StepPreparer", NativePreparer)


@pytest.fixture
def unwrapped_profile(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Stage 1 runs as a real subprocess with no OS-sandbox prefix; the Popen argv/env are recorded."""
    seen: list[list[str]] = []
    monkeypatch.setattr(L, "host_profile_argv", lambda kind, **kw: seen.append(["profile", kind]) or [])
    real = L.subprocess.Popen

    def spy(argv: list[str], **kw: Any) -> Any:
        seen.append(list(argv))
        seen.append([json.dumps(kw.get("env"))])
        return real(argv, **kw)
    monkeypatch.setattr(L.subprocess, "Popen", spy)
    return seen


def _stage0(root: Path, **kw: Any) -> Any:
    s = kw.pop("agent", None) or spec(root)
    kw.setdefault("stub_script", [text_reply("done")])
    return L.stage0(s, kw.pop("task", "Do the task."), project_root=root, budget=kw.pop("budget", Budget()),
                    guest_source=GUEST_SRC, guest_sha256=kw.pop("guest_sha256", GUEST_SHA),
                    resolver=kw.pop("resolver", FakeResolver()), **kw)


# --- pure units ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("requested, wrapped, want", [
    ("auto", False, "auto"), ("require", False, "require"), ("off", False, "off"), ("inherited", False, "auto"),
    ("auto", True, "off"), ("inherited", True, "inherited"), ("off", True, "off"),
])
def test_guest_os_confine_forwarding(requested: str, wrapped: bool, want: str) -> None:
    mode, note = L.guest_os_confine_for(requested, wrapped=wrapped)
    assert mode == want and (note is None) == (mode == requested)


def test_guest_require_under_a_wrapped_host_refuses() -> None:
    with pytest.raises(L.LaunchError, match="cannot nest"):
        L.guest_os_confine_for("require", wrapped=True)
    with pytest.raises(L.LaunchError):
        L.guest_os_confine_for("bogus", wrapped=False)


def test_scrubbed_env_keeps_only_path_lang_home_tmpdir() -> None:
    env = L.scrubbed_env({"PATH": "/bin", "LANG": "C", "HOME": "/h", "ANTHROPIC_API_KEY": KEY, "GH_TOKEN": "x",
                          "AWS_SECRET_ACCESS_KEY": "y", "PYTHONPATH": "/evil"}, "/t")
    assert env == {"PATH": "/bin", "LANG": "C", "HOME": "/h", "TMPDIR": "/t"}


def test_seatbelt_host_profile(tmp_path: Path) -> None:
    root = project(tmp_path)
    scratch = root / ".agentteams" / "wasm" / "scratch" / "primary-producer"
    scratch.mkdir(parents=True)
    tmp = tmp_path / "t"
    tmp.mkdir()
    argv = L.host_profile_argv("seatbelt", project_root=str(root), tmp_dir=str(tmp), scratch=str(scratch),
                               exec_literals=[os.path.realpath(sys.executable)], exec_paths=[])
    assert argv[:2] == ["/usr/bin/sandbox-exec", "-p"] and len(argv) == 3
    profile = argv[2]
    for store in L.HOST_DENY_READS:          # R5: credential stores and the secrets dir are read-denied
        assert f'(subpath "{os.path.realpath(os.path.expanduser(store))}")' in profile
    deny_at = profile.index("(deny file-read*\n")
    assert deny_at > 0
    writes = profile.split("(allow file-write*")
    assert len(writes) == 3                   # tmp (+ /dev nodes), then exactly one late allow: the scratch
    late = writes[2].split("\n)")[0].strip()
    assert late == f'(subpath "{os.path.realpath(scratch)}")'
    assert f'(subpath "{os.path.realpath(root / ".agentteams")}")' in profile    # control plane write-denied
    assert "(deny network*)" not in profile   # the host calls the provider
    assert f'(literal "{os.path.realpath(sys.executable)}")' in profile


def test_host_profile_refuses_a_scratch_outside_the_control_plane_shape(tmp_path: Path) -> None:
    root = project(tmp_path)
    for bad in (root / ".agentteams", root / ".agentteams" / "wasm-runs" / ("a" * 32), root / "docs"):
        with pytest.raises(L.LaunchError, match="refused"):
            L.host_profile_argv("seatbelt", project_root=str(root), tmp_dir=str(tmp_path), scratch=str(bad),
                                exec_literals=[], exec_paths=[])
    with pytest.raises(L.LaunchError, match="unknown"):
        L.host_profile_argv("jail", project_root=str(root), tmp_dir=str(tmp_path), scratch=None, exec_literals=[],
                            exec_paths=[])


def test_bwrap_host_profile_binds_only_tmp_and_scratch(tmp_path: Path) -> None:
    root = project(tmp_path)
    scratch = root / ".agentteams" / "wasm" / "scratch" / "primary-producer"
    scratch.mkdir(parents=True)
    argv = L.host_profile_argv("bwrap", project_root=str(root), tmp_dir=str(tmp_path), scratch=str(scratch),
                               exec_literals=[], exec_paths=[])
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"]
    assert binds == [os.path.realpath(tmp_path), os.path.realpath(scratch)]
    assert "--unshare-net" not in argv and argv[-1] == "--"


# --- stage0 refusals -----------------------------------------------------------------------------------------------

def test_unresolvable_runtime_refuses_before_anything(tmp_path: Path) -> None:
    class Missing:
        def resolve(self, component: str) -> Path:
            raise RuntimeError("pin incomplete (TODO-PIN)")
    root = project(tmp_path)
    res = _stage0(root, resolver=Missing())
    assert res.status == "refused" and res.reason.startswith("runtime:") and not (root / ".agentteams").exists()


@pytest.mark.parametrize("kw, why", [
    ({"agent_provider": "nonesuch"}, "unknown provider"),
    ({"agent_provider": "anthropic", "environ": {}}, "no API key"),
    ({"agent_provider": "anthropic", "environ": {"ANTHROPIC_API_KEY": KEY}}, "stub_script"),
    ({"host_confine": "sometimes"}, "host_confine"),
])
def test_stage0_refusals(tmp_path: Path, native: None, kw: dict, why: str) -> None:
    root = project(tmp_path)
    provider = kw.pop("agent_provider", "stub")
    res = _stage0(root, agent=spec(root, provider=provider), profiles=_fake_profiles(None), **kw)
    assert res.status == "refused" and why in res.reason


def test_host_confine_require_without_a_sandbox_refuses(tmp_path: Path, native: None) -> None:
    res = _stage0(project(tmp_path), host_confine="require", profiles=_fake_profiles(None))
    assert res.status == "refused" and "host_confine=require" in res.reason


def test_guest_source_must_match_its_pin(tmp_path: Path, native: None) -> None:
    res = _stage0(project(tmp_path), guest_sha256="0" * 64, profiles=_fake_profiles(None))
    assert res.status == "refused" and "pinned sha256" in res.reason


def test_a_caller_supplied_guest_is_refused_outside_tests(tmp_path: Path, native: None,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """@security P07 C1: only the pinned guest runs unless the test-only opt-in is set."""
    from agentteams.wasm_agent import guest_source as GS

    monkeypatch.delenv(L.TEST_GUEST_ENV, raising=False)
    res = _stage0(project(tmp_path), profiles=_fake_profiles(None))
    assert res.status == "refused" and "only the pinned guest program runs" in res.reason
    assert L.TEST_GUEST_ENV == "AGENTTEAMS_WASM_TEST_GUEST"   # the name tests/conftest.py sets
    root = project(tmp_path / "p")

    class NoRuntime:
        def resolve(self, name: str) -> Any:
            raise LookupError(f"{name} not installed")

    pinned = L.stage0(spec(root), "t", project_root=root, budget=Budget(), guest_source=GS.load_guest_source()[0],
                      guest_sha256=GS.GUEST_SHA256, resolver=NoRuntime())
    assert pinned.status == "refused" and pinned.reason.startswith("runtime:")   # got past the guest check


# --- in-process (no OS sandbox) ----------------------------------------------------------------------------------

def test_in_process_run_says_it_is_not_confined(tmp_path: Path, native: None) -> None:
    import io
    out = io.StringIO()
    root = project(tmp_path)
    res = _stage0(root, profiles=_fake_profiles(None), guest_os_confine="inherited", echo=out)
    assert (res.status, res.output) == ("ok", "done")
    assert any(n.startswith("WASM host not OS-confined: no usable OS sandbox") for n in res.notices)
    assert any("'inherited' downgraded to 'auto'" in n for n in res.notices)
    assert NativePreparer.last is not None and NativePreparer.last.os_confine == "auto"
    text = out.getvalue()
    assert "WASM host not OS-confined" in text and L.OUTPUT_HEADER in text and res.transcript_head in text
    start = json.loads(Path(res.transcript_path).read_text().splitlines()[0])
    assert any("not OS-confined" in n for n in start["notices"])
    assert verify_transcript(res.transcript_path).complete
    assert json.loads((Path(res.transcript_path).parent / "result.json").read_text())["status"] == "ok"


def test_os_switch_off_skips_host_confinement(tmp_path: Path, native: None) -> None:
    res = _stage0(project(tmp_path), profiles=_fake_profiles("seatbelt"), state=types.SimpleNamespace(os=False))
    assert res.status == "ok" and "WASM host not OS-confined: the OS sandbox switch is off" in res.notices


# --- wrapped: a real Stage 1 subprocess ----------------------------------------------------------------------------

def test_wrapped_stage1_runs_over_the_pipes(tmp_path: Path, native: None, unwrapped_profile: list) -> None:
    root = project(tmp_path)
    res = _stage0(root, profiles=_fake_profiles("seatbelt"), guest_os_confine="inherited")
    assert (res.status, res.reason, res.output) == ("ok", "done", "done"), res
    assert "WASM host OS-confined: nested:seatbelt" in res.notices
    assert NativePreparer.last.os_confine == "inherited"                 # forwarded only because it wrapped
    assert NativePreparer.last.prepared == ["agent", "agent"] and NativePreparer.last.released == ["t1", "t2"]
    rep = verify_transcript(res.transcript_path)
    assert rep.ok and rep.complete and rep.head == res.transcript_head
    kinds = [json.loads(x)["kind"] for x in Path(res.transcript_path).read_text().splitlines()]
    assert kinds[0] == "run-start" and kinds[-1] == "run-end" and "model-reply" in kinds


def test_wrapped_auto_becomes_off_and_is_recorded(tmp_path: Path, native: None, unwrapped_profile: list) -> None:
    res = _stage0(project(tmp_path), profiles=_fake_profiles("seatbelt"))
    assert res.status == "ok" and NativePreparer.last.os_confine == "off"
    assert any("without a fresh one" in n for n in res.notices)


def test_wrapped_guest_env_is_empty(tmp_path: Path, native: None, unwrapped_profile: list) -> None:
    res = _stage0(project(tmp_path), profiles=_fake_profiles("seatbelt"), stub_script=[text_reply("ENV")])
    assert res.reason == "guest:env-0", res.reason


class _Upstream:
    """A loopback OpenAI-compatible endpoint that records the Authorization header it receives."""

    def __init__(self) -> None:
        self.auth: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:   # noqa: N802 - http.server API
                outer.auth.append(self.headers.get("Authorization", ""))
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = json.dumps({"id": "r1", "choices": [{"message": {"role": "assistant", "content": "hi"},
                                                            "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a: object) -> None:
                pass
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


def test_key_custody_through_a_wrapped_stage1(tmp_path: Path, native: None, unwrapped_profile: list) -> None:
    up = _Upstream()
    try:
        root = project(tmp_path)
        environ = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
                   "AGENTTEAMS_MODEL_API_KEY": KEY}
        res = _stage0(root, agent=spec(root, provider="openai-compatible"), environ=environ, base_url=up.url,
                      stub_script=None, profiles=_fake_profiles("seatbelt"))
    finally:
        up.httpd.shutdown()
    assert (res.status, res.output) == ("ok", "hi"), res
    assert up.auth == [f"Bearer {KEY}"]                       # the key reached the provider, via the key pipe
    popen = [a for a in unwrapped_profile if a and a[0] != "profile"]
    argv, env = popen[0], json.loads(popen[1][0])
    assert KEY not in " ".join(argv) and KEY not in json.dumps(env)       # not in Stage 1's argv or env
    assert "--key-fd" in argv and set(env) <= {"PATH", "LANG", "HOME", "TMPDIR"}
    blob = b"".join(p.read_bytes() for p in (root / ".agentteams").rglob("*") if p.is_file())
    assert KEY.encode() not in blob                            # nor in any transcript/result file


# --- the bootstrap line (A1, H3, H4) -------------------------------------------------------------------------------

def _popen_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    procs: list[Any] = []
    monkeypatch.setattr(L, "host_profile_argv", lambda kind, **kw: [])
    real = L.subprocess.Popen

    def spy(argv: list[str], **kw: Any) -> Any:
        procs.append(real(argv, **kw))
        return procs[-1]
    monkeypatch.setattr(L.subprocess, "Popen", spy)
    return procs


def _kinds_and_codes(res: Any) -> list[tuple[str, Any]]:
    recs = [json.loads(x) for x in Path(res.transcript_path).read_text().splitlines()]
    return [(r["kind"], r.get("code")) for r in recs]


def test_an_oversize_bootstrap_refuses_and_reaps_stage1(tmp_path: Path, native: None,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    procs = _popen_spy(monkeypatch)
    monkeypatch.setattr(P, "MAX_CONTROL_BYTES", 512)    # Stage 0 side only; Stage 1 is a separate process
    res = _stage0(project(tmp_path), profiles=_fake_profiles("seatbelt"), task="x" * 4096)
    assert (res.status, res.reason) == ("refused", "host-launch: bootstrap frame"), res
    assert len(procs) == 1 and procs[0].returncode is not None          # killed and waited, not left running
    assert ("protocol-error", "boot-frame") in _kinds_and_codes(res)


def test_a_key_pipe_write_failure_is_journaled(tmp_path: Path, native: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _popen_spy(monkeypatch)
    real = P.write_line

    def write_line(fd: int, message: Any) -> None:
        if "mac_key" in message:
            raise BrokenPipeError("stage 1 went away")
        real(fd, message)
    monkeypatch.setattr(P, "write_line", write_line)
    res = _stage0(project(tmp_path), profiles=_fake_profiles("seatbelt"))
    assert (res.status, res.reason) == ("failed", "host-exited"), res
    assert ("protocol-error", "key-pipe") in _kinds_and_codes(res)


def test_the_bootstrap_carries_exactly_the_shared_key_set(tmp_path: Path, native: None,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    _popen_spy(monkeypatch)
    seen: list[set[str]] = []
    real = P.write_line

    def write_line(fd: int, message: Any) -> None:
        if "mac_key" in message:
            seen.append(set(message))
            assert message["v"] == P.BOOTSTRAP_VERSION
        real(fd, message)
    monkeypatch.setattr(P, "write_line", write_line)
    assert _stage0(project(tmp_path), profiles=_fake_profiles("seatbelt")).status == "ok"
    assert seen == [set(P.BOOT_KEYS)]


@pytest.mark.parametrize("payload", [b"not json\n", b'{"v": 1}\n', b'{"v": 1', None], ids=["json", "keys", "mid-line",
                                                                                         "bad-fd"])
def test_stage1_main_returns_2_for_an_unusable_bootstrap(payload: bytes | None) -> None:
    from agentteams.wasm_agent import stage1 as S1
    r, w = os.pipe()
    if payload is None:
        os.close(r)                    # read_line then fails with OSError (EBADF)
    else:
        os.write(w, payload)
    os.close(w)
    assert S1.main(["--key-fd", str(r), "--ctl-in", "0", "--ctl-out", "1"]) == 2


def test_no_wasm_agent_module_reaches_into_the_executors_privates() -> None:
    pkg = Path(L.__file__).parent
    for py in pkg.glob("*.py"):
        assert "._execute" not in py.read_text(), py.name   # brokers use the public wasm_exec.execute_launch
