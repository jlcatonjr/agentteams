"""WASM plan P12: the runner's ``sandbox: wasm`` jails (``confined_programs.<agent>.sandbox = "wasm"``).

Unit cases use a fake wasmtime (a Python script that prints its argv and, on request, writes files) behind a fake
resolver, and force the logged ``allow_unconfined_runs`` opt-out so nothing needs a nestable OS sandbox. The live
cases (the Seatbelt + wasmtime JIT spike, plan step 1) need the dev wasmtime (``AGENTTEAMS_WASM_DEV_WASMTIME``) and a
usable OS sandbox, and skip otherwise (Seatbelt cannot nest inside a sandboxed session).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import confinement as C
from agentteams import proposal_run as PR
from agentteams import proposals as P
from agentteams import runner_wasm as RW
from agentteams import wasm_exec as W
from tests._wasm_helpers import PROBE, dev_wasmtime
from tests.test_sandbox_relaxation_gate import stub_ed25519  # noqa: F401  (fixture)

PY = os.path.realpath(sys.executable)
CORE = b"\0asm\x01\0\0\0" + b"\0" * 24

FAKE_WASMTIME = f"""#!{PY}
import json, os, sys
argv = sys.argv[1:]
dirs = dict(a.split("::", 1)[::-1] for a in argv if "::" in a)
guest = argv[argv.index("--") + 1:]
if "WRITE" in guest:
    open(os.path.join(dirs["/scratch"], "out.txt"), "w").write("scratch")
if "ESCAPE" in guest:
    proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(dirs["/scratch"]))))
    open(os.path.join(proj, "escaped.txt"), "w").write("x")
print(json.dumps({{"argv": argv}}))
"""


class FakeRuntime:
    """wasmtime + a fake python.wasm (+ lib) installed under ``.agentteams/wasm``; hashes checked on resolve."""

    def __init__(self, root: Path, *, outside: Path | None = None) -> None:
        base = (outside or root / ".agentteams" / "wasm")
        wt = base / "wasmtime" / "36.0.20" / "wasmtime"
        py = root / ".agentteams" / "wasm" / "python" / "3.14" / "python.wasm"
        for p in (wt, py):
            p.parent.mkdir(parents=True, exist_ok=True)
        wt.write_text(FAKE_WASMTIME)
        wt.chmod(0o755)
        py.write_bytes(CORE)
        (py.parent / "lib").mkdir(exist_ok=True)
        self.paths = {"wasmtime": wt, "python": py}
        self.sha = {k: hashlib.sha256(v.read_bytes()).hexdigest() for k, v in self.paths.items()}

    def resolve(self, component: str) -> Path:
        path = self.paths[component]
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.sha[component]:
            raise RuntimeError(f"{component}: {path} does not match its pin")
        return path

    def expected_sha256(self, component: str) -> str:
        return self.sha[component]

    def runtime_mounts(self, component: str) -> tuple:
        return ((self.paths["python"].parent / "lib", "/lib"),) if component == "python" else ()

    def resolve_tree(self, component: str) -> Path:
        return self.paths[component].parent

    def pinned_version(self, component: str) -> str:
        return "36.0.20"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv(P.KEY_ENV[0], "test-key-0123456789abcdef0123456")
    root = Path(os.path.realpath(tmp_path)) / "proj"
    for d in ("tools", "data", "src"):
        (root / d).mkdir(parents=True)
    (root / "tools" / "s.py").write_text("print('hi')\n")
    (root / "tools" / "m.wasm").write_bytes(CORE)
    (root / "data" / "in.csv").write_text("a\n")
    (root / "src" / "a.txt").write_text("a\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    monkeypatch.setattr(C, "available", lambda: None)  # unit cases: the logged opt-out, no OS sandbox
    return root


@pytest.fixture
def runtime(project, monkeypatch):
    rt = FakeRuntime(project)
    monkeypatch.setattr(RW, "_resolver", lambda root: rt)
    return rt


def _jail(**wasm):
    return {"sandbox": "wasm", "wasm": {"languages": ["python", "module"], "read": ["tools", "data"], **wasm}}


def _policy(prefix, args=(), *, jail=None, extra=None, agent="x"):
    return P.load_policy({
        "agent_policies": {agent: {"commands": [{"prefix": list(prefix), "args": list(args)}]}},
        "confined_programs": {agent: jail or _jail()}, "allow_unconfined_runs": True, **(extra or {})})


def _req(root, argv, agent="x"):
    return {"kind": "command-request", "dispatch": P.issue_dispatch(root, agent), "argv": list(argv),
            "purpose": "test"}


def _run(root, argv, policy, **kw):
    return P.run_request(_req(root, argv), root=root, policy=policy, confine=True, **kw)


def _ledger(root):
    path = root / P.LEDGER_REL
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def spy(monkeypatch):
    calls = []
    real = subprocess.Popen

    def popen(argv, **kw):
        if "run" in argv[:6] and "cache=n" in argv:   # only the wasmtime launch, not the snapshot's git calls
            calls.append({"argv": list(argv), **kw})
        return real(argv, **kw)

    monkeypatch.setattr(PR.subprocess, "Popen", popen)
    return calls


# --- entry shape (load_confined) ----------------------------------------------------------------------------------

def _load(spec, agent="x"):
    from agentteams.frameworks._write_roots import control_plane_of
    return C.load_confined({agent: spec}, {}, control_plane_of)


def test_load_confined_accepts_wasm_entry():
    jail = _load({"sandbox": "wasm", "wasm": {"languages": ["python"], "read": ["data/inputs"], "scratch": True,
                                              "limits": {"fuel": 2_000_000_000, "memory_mib": 512, "timeout_s": 60}}})
    assert jail["x"] == {"sandbox": "wasm", "exec": [], "write": [], "wasm": {
        "languages": ["python"], "read": ["data/inputs"], "scratch": True,
        "limits": {"fuel": 2_000_000_000, "max_memory_bytes": 512 << 20, "timeout_s": 60}}}


def test_load_confined_wasm_defaults_to_pinned_limits_and_no_scratch():
    w = _load({"sandbox": "wasm", "wasm": {"languages": ["module"]}})["x"]["wasm"]
    d = W.DEFAULT_LIMITS
    assert w["scratch"] is False and w["read"] == []
    assert w["limits"] == {"fuel": d.fuel, "max_memory_bytes": d.max_memory_bytes, "timeout_s": d.timeout_s}


@pytest.mark.parametrize("spec, needle", [
    ({"sandbox": "wasm", "exec": ["/usr/bin"], "wasm": {"languages": ["python"]}}, "no exec"),
    ({"sandbox": "wasm", "write": ["out"], "wasm": {"languages": ["python"]}}, "no write"),
    ({"sandbox": "wasm", C.OPT_IN_LAUNCHER_STORE: True, "wasm": {"languages": ["python"]}}, "only sandbox and wasm"),
    ({"sandbox": "wasm", "wasm": {"languages": ["python"], "scratch": "yes"}}, "scratch must be true or false"),
    ({"sandbox": "wasm", "wasm": {"languages": ["python"], "scratch": 1}}, "scratch must be true or false"),
    ({"sandbox": "wasm", "wasm": {"languages": ["ruby"]}}, "languages"),
    ({"sandbox": "wasm", "wasm": {"languages": []}}, "languages"),
    ({"sandbox": "wasm", "wasm": {"languages": ["python", "python"]}}, "languages"),
    ({"sandbox": "wasm", "wasm": {"languages": ["python"], "limits": {"fuel": 0}}}, "limits"),
    ({"sandbox": "wasm", "wasm": {"languages": ["python"], "limits": {"timeout_s": 10**9}}}, "limits"),
    ({"sandbox": "wasm", "wasm": {"languages": ["python"], "limits": {"memory_mib": 10**6}}}, "limits"),
    ({"sandbox": "wasm", "wasm": {"languages": ["python"], "limits": {"fuel": True}}}, "integers"),
    ({"sandbox": "wasm", "wasm": {"languages": ["python"], "limits": {"cpu": 1}}}, "takes only"),
    ({"sandbox": "wasm", "wasm": {"languages": ["python"], "net": True}}, "must be an object"),
    ({"sandbox": "wasm"}, "must be an object"),
    ({"sandbox": "vm", "exec": ["/usr/bin"]}, "sandbox must be one of"),
    ({"exec": ["/usr/bin"], "wasm": {"languages": ["python"]}}, "exec, write"),
])
def test_load_confined_refuses_bad_wasm_entries(spec, needle):
    with pytest.raises(C.ConfinementError, match=needle):
        _load(spec)


@pytest.mark.parametrize("rel", [".agentteams/wasm", ".git", ".claude/agents", "data/.env", "keys/id_rsa",
                                 "conf/.aws", "a/../b", "/abs", "~/x", "data/*", ".", "x\\y", "secrets.pem"])
def test_load_confined_refuses_read_on_secret_or_control_plane(rel):
    with pytest.raises(C.ConfinementError):
        _load({"sandbox": "wasm", "wasm": {"languages": ["python"], "read": [rel]}})


def test_load_confined_refuses_a_non_slug_wasm_agent():
    with pytest.raises(C.ConfinementError, match="plain slug"):
        _load({"sandbox": "wasm", "wasm": {"languages": ["python"]}}, agent="Bad_Agent")


def test_load_confined_os_entries_unchanged():
    spec = {"exec": ["/usr/bin"], "write": ["out"], C.OPT_IN_LAUNCHER_STORE: True}
    assert _load(spec) == {"x": {"exec": ["/usr/bin"], "write": ["out"], C.OPT_IN_LAUNCHER_STORE: True}}
    assert _load({**spec, "sandbox": "os"}) == _load(spec)          # an explicit "os" is the default shape
    assert _load({"exec": ["/usr/bin"]}) == {"x": {"exec": ["/usr/bin"], "write": []}}


def test_deny_lists_match_the_runner_mcp_server():
    import ast
    src = (Path(RW.__file__).parent / "data" / "agentteams-runner-mcp.py").read_text()
    consts = {t.id: ast.literal_eval(n.value) for n in ast.parse(src).body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name) and t.id.startswith("DENY_")}
    for name in ("DENY_PARTS", "DENY_SUFFIXES", "DENY_NAMES", "DENY_PREFIXES", "DENY_INFIXES", "DENY_ALLOWED"):
        assert getattr(RW, name) == consts[name], name


def test_scratch_rel_matches_sandbox_state():
    from agentteams.sandbox_state import SCRATCH_REL
    assert RW.SCRATCH_REL == SCRATCH_REL


# --- argv shape and cross-over refusals -------------------------------------------------------------------------

def test_wasm_agent_rejects_host_argv0(project, runtime):
    policy = _policy([PY, "-c", "pass"])
    with pytest.raises(P.ProposalError, match="argv\\[0\\] must be one of"):
        _run(project, [PY, "-c", "pass"], policy)


def test_os_agent_rejects_wasm_argv0(project, runtime):
    policy = _policy(["wasm:python", "tools/s.py"], jail={"exec": [os.path.dirname(PY)], "write": []})
    with pytest.raises(P.ProposalError, match="not on sandbox: wasm"):
        _run(project, ["wasm:python", "tools/s.py"], policy)


def test_wasm_agent_refused_outside_the_runner(project, runtime):
    policy = _policy(["wasm:python", "tools/s.py"])
    with pytest.raises(P.ProposalError, match="only through the runner"):
        P.run_request(_req(project, ["wasm:python", "tools/s.py"]), root=project, policy=policy)


def test_language_not_granted_refused(project, runtime):
    policy = _policy(["wasm:javascript", "tools/s.js"])
    (project / "tools" / "s.js").write_text("1")
    with pytest.raises(P.ProposalError, match="does not grant javascript"):
        _run(project, ["wasm:javascript", "tools/s.js"], policy)


@pytest.mark.parametrize("path, needle", [("src/a.txt", "a python guest must be"), ("src/b.py", "outside"),
                                          ("tools/../src/b.py", "allowlist|literal"), ("tools/link.py", "symlink"),
                                          ("tools/none.py", "cannot be read"), ("/etc/x.py", "project-relative")])
def test_module_path_outside_read_refused(project, runtime, path, needle):
    (project / "src" / "b.py").write_text("x")
    (project / "tools" / "link.py").symlink_to(project / "src" / "b.py")
    policy = _policy(["wasm:python", path])
    with pytest.raises(P.ProposalError, match=needle):
        _run(project, ["wasm:python", path], policy)


def test_argv_shape_asserted_no_env_no_network_no_extra_dir(project, runtime, spy):
    result = _run(project, ["wasm:python", "tools/s.py", "data.csv"],
                  _policy(["wasm:python", "tools/s.py"], ["[a-z.]+"]))
    assert result["exit"] == 0, result
    argv = json.loads(result["stdout"])["argv"]
    assert argv[0] == "run" and argv[1:3] == ["-C", "cache=n"]
    assert "inherit-env=n" in argv[6] and "inherit-network=n" in argv[6] and "--env" not in argv
    dirs = [argv[i + 1].split("::")[1] for i, a in enumerate(argv) if a == "--dir"]
    assert dirs == ["/lib", "/src", "/project/tools", "/project/data"]
    assert argv[-5:] == ["-I", "-S", "-B", "/src/main.py", "data.csv"]
    assert not any(a.startswith("-S") and "inherit-network=y" in a for a in argv)


def test_wasmtime_env_is_empty(project, runtime, spy):
    _run(project, ["wasm:python", "tools/s.py"], _policy(["wasm:python", "tools/s.py"]))
    assert spy[0]["env"] == {} and spy[0]["start_new_session"] is True


def test_module_language_runs_the_module_from_the_project(project, runtime, spy):
    result = _run(project, ["wasm:module", "tools/m.wasm"], _policy(["wasm:module", "tools/m.wasm"]))
    assert result["exit"] == 0, result
    module = json.loads(result["stdout"])["argv"][-1]
    assert module.startswith(str(project / ".agentteams" / "wasm" / "exec")) and module.endswith("/bin/module.wasm")


# --- R3': where executables run from ---------------------------------------------------------------------------

def test_verified_copies_in_agentteams_exec_never_tmpdir(project, monkeypatch, tmp_path, spy):
    rt = FakeRuntime(project, outside=Path(os.path.realpath(tmp_path)) / "elsewhere")
    monkeypatch.setattr(RW, "_resolver", lambda root: rt)
    _run(project, ["wasm:python", "tools/s.py"], _policy(["wasm:python", "tools/s.py"]))
    exe = spy[0]["argv"][0]
    assert re.match(re.escape(str(project / ".agentteams/wasm/exec")) + r"/[0-9a-f]{16}/bin/wasmtime$", exe)
    assert not exe.startswith(os.path.realpath(tempfile.gettempdir()) + "/agentteams-run-")
    assert not (project / ".agentteams" / "wasm" / "exec").exists() or not any(
        (project / ".agentteams" / "wasm" / "exec").iterdir())   # removed after the run


def test_in_place_wasmtime_runs_from_the_install_dir(project, runtime, spy):
    _run(project, ["wasm:python", "tools/s.py"], _policy(["wasm:python", "tools/s.py"]))
    assert spy[0]["argv"][0] == str(runtime.paths["wasmtime"])   # R3': run in place, re-hashed just before exec


def test_mismatched_wasmtime_hash_refuses_before_ledger_row(project, runtime):
    runtime.paths["wasmtime"].write_text(FAKE_WASMTIME + "# planted\n")
    with pytest.raises(P.ProposalError, match="does not match its pin"):
        _run(project, ["wasm:python", "tools/s.py"], _policy(["wasm:python", "tools/s.py"]))
    assert [r["action"] for r in _ledger(project)] == ["run-request-refused"] or \
        all(r["action"] != "run-request-start" for r in _ledger(project))


def test_check_roots_refuses_exec_in_tmpdir(project, tmp_path):
    """Pins the confinement.check_roots behaviour R3 relies on: an exec path inside the run's TMPDIR refuses."""
    run_tmp = tmp_path / "agentteams-run-x"
    run_tmp.mkdir()
    with pytest.raises(C.ConfinementError, match="overlap"):
        C.check_roots(project, [str(run_tmp / "wasmtime")], [], run_tmp)


# --- ledger and results ---------------------------------------------------------------------------------------

def test_ledger_rows_carry_sandbox_and_hashes(project, runtime):
    result = _run(project, ["wasm:python", "tools/s.py"], _policy(["wasm:python", "tools/s.py"]))
    rows = [r for r in _ledger(project) if r["action"] in ("run-request-start", "run-request")]
    assert [r["action"] for r in rows] == ["run-request-start", "run-request"]
    src = hashlib.sha256((project / "tools" / "s.py").read_bytes()).hexdigest()
    for row in rows:
        assert row["sandbox"] == "wasm" and row["confined"] == "unconfined-opt-out+wasm"
        w = row["wasm"]
        assert w["language"] == "python" and w["source_sha256"] == src
        assert w["wasmtime_sha256"] == runtime.sha["wasmtime"] and w["module_sha256"] == runtime.sha["python"]
        assert re.fullmatch(r"[0-9a-f]{64}", w["grant_sha256"]) and w["os_confinement"] == "none"
    assert "trap" in result


def test_ledger_os_confinement_values(project, runtime, monkeypatch):
    seen = {}

    def fake_launch(cmd, root, *, sandbox):
        seen["sandbox"] = sandbox
        return W.materialize(cmd.prepared, list(cmd.guest_args), project_root=root, os_confine="off",
                             resolver=cmd.resolver, profiles=SP, control_plane_protected=True)

    monkeypatch.setattr(RW, "launch", fake_launch)
    monkeypatch.setattr(C, "available", lambda: "seatbelt")
    _run(project, ["wasm:python", "tools/s.py"], _policy(["wasm:python", "tools/s.py"]))
    start = [r for r in _ledger(project) if r["action"] == "run-request-start"][-1]
    assert seen["sandbox"] == "seatbelt" and start["confined"] == "seatbelt+wasm"
    assert start["wasm"]["os_confinement"] == "nested"


def test_unconfined_opt_out_still_runs_in_wasm_with_notice(project, runtime, capsys):
    result = _run(project, ["wasm:python", "tools/s.py"], _policy(["wasm:python", "tools/s.py"]))
    assert json.loads(result["stdout"])["argv"][0] == "run"
    assert "[runner] WASM only: no OS sandbox" in capsys.readouterr().err


def test_no_opt_out_and_no_sandbox_refuses(project, runtime):
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": ["wasm:python", "tools/s.py"]}]}},
                            "confined_programs": {"x": _jail()}})
    with pytest.raises(P.ProposalError, match="no usable OS sandbox"):
        _run(project, ["wasm:python", "tools/s.py"], policy)


def test_timeout_s_never_exceeds_the_runner_cap(project, runtime):
    jail = C.load_confined({"x": _jail(limits={"timeout_s": 3000})}, {}, lambda r: None)["x"]
    cmd = RW.prepare_command(project, "x", ["wasm:python", "tools/s.py"], jail, timeout_s=120, resolver=runtime)
    assert cmd.prepared.grant.limits.timeout_s == 120


# --- scratch: R8 / R11 / Rule-12 carve-outs 3 and 4 -------------------------------------------------------------

def _seatbelt_launch(project, runtime, monkeypatch, **wasm):
    monkeypatch.setattr(SP, "available", lambda: "seatbelt")
    jail = C.load_confined({"x": _jail(**wasm)}, {}, lambda r: None)["x"]
    cmd = RW.prepare_command(project, "x", ["wasm:python", "tools/s.py"], jail, timeout_s=60, resolver=runtime)
    launch = RW.launch(cmd, project, sandbox="seatbelt")
    try:
        assert launch.argv[:2] == ["/usr/bin/sandbox-exec", "-p"]
        return launch.argv[2]
    finally:
        launch.cleanup()


def _exec_block(profile):
    return profile.split("(allow process-exec*", 1)[1].split("\n)\n", 1)[0]


def test_seatbelt_profile_has_exactly_one_exec_literal(project, runtime, monkeypatch):
    block = _exec_block(_seatbelt_launch(project, runtime, monkeypatch))
    assert re.findall(r'\((literal|subpath) "([^"]+)"\)', block) == [("literal", str(runtime.paths["wasmtime"]))]


def test_scratch_allow_is_exactly_the_agent_scratch(project, runtime, monkeypatch):
    profile = _seatbelt_launch(project, runtime, monkeypatch, scratch=True)
    protected_deny = profile.index("(deny file-write*\n")
    late = re.findall(r'\(subpath "([^"]+)"\)', profile[protected_deny:].split("(deny file-read*", 1)[0]
                      .split("(allow file-write*", 1)[1])
    assert late == [str(project / ".agentteams" / "wasm" / "scratch" / "x")]   # R11: nothing else re-opened
    first_allow = profile.split("(deny file-write*\n", 1)[0]
    assert ".agentteams" not in first_allow.replace(str(project / ".agentteams" / "wasm" / "exec"), "")


def test_scratch_allow_absent_without_scratch_and_for_os_jails(project, runtime, monkeypatch):
    assert profile_allow_count(_seatbelt_launch(project, runtime, monkeypatch)) == 1
    os_profile = C.wrap(["/usr/bin/true"], sandbox="seatbelt", root=project, cwd=project, exec_paths=["/usr/bin"],
                        write_roots=[], tmp_dir=project / "src", resolver=lambda name: [])[2]
    assert profile_allow_count(os_profile) == 1 and "wasm/scratch" not in os_profile


def profile_allow_count(profile):
    return profile.count("(allow file-write*")


def test_scratch_path_must_be_the_agent_scratch(project, runtime):
    grant = W.Grant(mounts=(W.Mount(str(project / "src"), "/scratch", "rw"),), limits=W.DEFAULT_LIMITS)
    prepared = W.prepare(runtime.paths["python"], grant=grant)
    with pytest.raises(W.WasmExecError, match="scratch"):
        W.materialize(prepared, [], project_root=project, os_confine="off", resolver=runtime, profiles=SP)


def test_scratch_writes_declared_other_writes_undeclared(project, runtime, monkeypatch):
    def unwrapped(cmd, root, *, sandbox):   # stand-in for the nested wrap (cannot nest here); the diff logic is real
        return W.materialize(cmd.prepared, list(cmd.guest_args), project_root=root, os_confine="off",
                             resolver=cmd.resolver, profiles=SP, control_plane_protected=True)

    monkeypatch.setattr(RW, "launch", unwrapped)
    monkeypatch.setattr(C, "available", lambda: "seatbelt")
    policy = _policy(["wasm:python", "tools/s.py"], ["WRITE|ESCAPE"], jail=_jail(scratch=True))
    ok = _run(project, ["wasm:python", "tools/s.py", "WRITE"], policy)
    assert ok["undeclared_writes"] == [] and (project / ".agentteams/wasm/scratch/x/out.txt").read_text() == "scratch"
    with pytest.raises(P.UndeclaredWritesError) as exc:
        _run(project, ["wasm:python", "tools/s.py", "ESCAPE"], policy)
    assert exc.value.result["undeclared_writes"] == ["escaped.txt"]


def test_scratch_writes_undeclared_under_the_opt_out(project, runtime):
    policy = _policy(["wasm:python", "tools/s.py"], ["WRITE"], jail=_jail(scratch=True))
    with pytest.raises(P.UndeclaredWritesError) as exc:
        _run(project, ["wasm:python", "tools/s.py", "WRITE"], policy)
    assert exc.value.result["undeclared_writes"] == [".agentteams/wasm/scratch/x/out.txt"]


# --- runner start, governance ---------------------------------------------------------------------------------

def test_check_runtimes_refuses_when_a_wasm_jail_runtime_is_absent(project):
    from agentteams import proposal_runner as R
    policy = _policy(["wasm:python", "tools/s.py"])
    with pytest.raises(R.RunnerError, match="python runtime is not usable|wasmtime runtime is not usable"):
        R.check_wasm_runtimes(project, policy)


def test_check_runtimes_passes_with_the_runtime_and_ignores_os_jails(project, runtime):
    RW.check_runtimes(project, C.load_confined({"x": _jail(), "y": {"exec": ["/usr/bin"]}}, {}, lambda r: None),
                      resolver=runtime)
    RW.check_runtimes(project, {"y": {"exec": ["/usr/bin"], "write": []}}, resolver=object())  # nothing resolved


def test_program_modes_and_exceptions():
    raw = {"a": {"sandbox": "wasm"}, "b": {"exec": ["/x"]}, "c": {"sandbox": "os"}, "gate_exec": {}, "Bad": {}}
    exc = RW.runner_exceptions(raw)
    assert exc.wasm_programs == {"a"} and exc.os_programs == {"b", "c"}


def test_switch_relaxations_find_the_wasm_to_os_switch():
    prev = RW.runner_exceptions({"a": {"sandbox": "wasm"}, "b": {"exec": ["/x"]}})
    new = RW.runner_exceptions({"a": {"exec": ["/x"]}, "b": {"exec": ["/x"]}})
    rel = RW.switch_relaxations(prev, new)
    assert [(r.action, r.continuing) for r in rel] == [("wasm-to-os-switch:a", False),
                                                       ("wasm-to-os-switch:b", True)]
    assert RW.switch_relaxations(prev, prev) == [r for r in RW.switch_relaxations(prev, prev) if r.continuing]


def test_switch_without_a_team_dir_refuses(tmp_path):
    with pytest.raises(RW.RunnerWasmError, match="no team directory"):
        RW.gate_policy_change(tmp_path, {"a": {"sandbox": "wasm"}}, {"a": {"exec": ["/x"]}})
    assert RW.gate_policy_change(tmp_path, {"a": {"exec": ["/x"]}}, {"a": {"exec": ["/y"]}}) == []


def test_wasm_to_os_switch_calls_the_p15_gate(tmp_path, capsys):
    from tests.test_sandbox_relaxation_gate import _team
    from agentteams.cli.sandbox_relaxation_gate import SandboxRelaxationError  # noqa: F401
    governed = _team(tmp_path / "g")
    with pytest.raises(RW.RunnerWasmError, match="wasm-to-os-switch:a"):
        RW.gate_policy_change(tmp_path, {"a": {"sandbox": "wasm"}}, {"a": {"exec": ["/x"]}}, output=governed)
    ungoverned = _team(tmp_path / "u", governed=False)
    recs = RW.gate_policy_change(tmp_path, {"a": {"sandbox": "wasm"}}, {"a": {"exec": ["/x"]}}, output=ungoverned)
    assert [r["authorization"] for r in recs] == ["notice-only"]
    assert "SANDBOX RELAXATION: wasm-to-os-switch:a" in capsys.readouterr().err


def _render(team, root, brief_confined):
    from agentteams.cli import artifacts as A
    from agentteams.sandbox_state import effective_sandbox_state
    m = {"write_policy": "orchestrator-only", "privilege_profile": "confined"}
    A._sandbox_relaxation_gate(m, effective_sandbox_state(m), "claude", root, team_dir=team, dry_run=False,
                               brief_confined_programs=brief_confined)
    return m


def _record_build_log(team, m):
    (team / "references" / "build-log.json").write_text(json.dumps(
        {k: m[k] for k in ("sandbox_state", "sandbox_exceptions") if k in m}))


def test_render_records_runner_programs_and_gates_the_switch(tmp_path, monkeypatch, stub_ed25519):
    from agentteams.cli.sandbox_relaxation_gate import SandboxRelaxationError
    from tests.test_sandbox_relaxation_gate import _log, _row, _team
    monkeypatch.setattr(C, "read_confined_file", lambda root: None)
    team = _team(tmp_path)
    m = _render(team, tmp_path, {"a": _jail(), "b": {"exec": ["/usr/bin"]}})
    assert m["sandbox_exceptions"] == {"native_exec_keep": [], "wasm_programs": ["a"], "os_programs": ["b"]}
    _record_build_log(team, m)
    with pytest.raises(SandboxRelaxationError, match="wasm-to-os-switch:a"):
        _render(team, tmp_path, {"a": {"exec": ["/usr/bin"]}, "b": {"exec": ["/usr/bin"]}})
    _log(team, _row("wasm-to-os-switch:a"))
    m2 = _render(team, tmp_path, {"a": {"exec": ["/usr/bin"]}, "b": {"exec": ["/usr/bin"]}})
    assert [r["action"] for r in m2["sandbox_relaxations"]] == ["wasm-to-os-switch:a"]
    _record_build_log(team, m2)   # the next render carries the switch over under its registered exception
    m3 = _render(team, tmp_path, {"a": {"exec": ["/usr/bin"]}, "b": {"exec": ["/usr/bin"]}})
    assert [r["authorization"] for r in m3["sandbox_relaxations"]] == ["active-exception"]


def test_render_without_wasm_programs_is_not_engaged(tmp_path, monkeypatch):
    from tests.test_sandbox_relaxation_gate import _team
    monkeypatch.setattr(C, "read_confined_file", lambda root: None)
    m = _render(_team(tmp_path), tmp_path, {"b": {"exec": ["/usr/bin"]}})
    assert "sandbox_exceptions" not in m and "sandbox_state" not in m   # byte-identical build-log (Rule 12)


# --- live: the Seatbelt + wasmtime JIT spike (plan step 1) -----------------------------------------------------

class DevRuntime:
    """The dev wasmtime (not the 36.0.20 pin, D9) behind the resolver protocol; copied, so the exec literal is the
    verified copy under ``.agentteams/wasm/exec/<run-id>/bin`` (the R3 path), as on a host without R1."""

    def __init__(self) -> None:
        self.wasmtime = dev_wasmtime()
        self.sha = hashlib.sha256(Path(self.wasmtime).read_bytes()).hexdigest() if self.wasmtime else ""

    def resolve(self, component):
        return Path(self.wasmtime)

    def expected_sha256(self, component):
        return self.sha

    def runtime_mounts(self, component):
        return ()

    def pinned_version(self, component):
        return "dev"


live = pytest.mark.skipif(dev_wasmtime() is None or SP.available() is None,
                          reason="needs the dev wasmtime and a usable (nestable) OS sandbox: run outside any session")


def _live_project(tmp_path, monkeypatch, args=(), **wasm):
    monkeypatch.setenv(P.KEY_ENV[0], "test-key-0123456789abcdef0123456")
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "probe.wasm").write_bytes(PROBE.read_bytes())
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.setattr(RW, "_resolver", lambda r: DevRuntime())
    jail = {"sandbox": "wasm", "wasm": {"languages": ["module"], "read": ["tools"], **wasm}}
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": ["wasm:module", "tools/probe.wasm"],
                                                                   "args": list(args)}]}},
                            "confined_programs": {"x": jail}})
    return root, policy


def _live_run(root, policy, *args):
    t0 = time.monotonic()
    result = P.run_request(_req(root, ["wasm:module", "tools/probe.wasm", *args]), root=root, policy=policy,
                           confine=True)
    print(f"SPIKE seatbelt+wasmtime {args or ('deny',)}: exit={result['exit']} trap={result.get('trap')} "
          f"wall={time.monotonic() - t0:.2f}s", file=sys.stderr)
    return result


@live
def test_live_seatbelt_plus_wasm_module_runs(tmp_path, monkeypatch):
    root, policy = _live_project(tmp_path, monkeypatch)
    result = _live_run(root, policy)
    start = [r for r in _ledger(root) if r["action"] == "run-request-start"][-1]
    assert start["confined"] in ("seatbelt+wasm", "bwrap+wasm") and start["wasm"]["os_confinement"] == "nested"
    assert result["exit"] == 0, result["stderr"]     # Cranelift's JIT mappings work under the nested profile
    assert "ENV0" in result["stdout"] and "SOCK0" in result["stdout"] and result["undeclared_writes"] == []


@live
def test_live_guest_cannot_write_its_read_mount(tmp_path, monkeypatch):
    root, policy = _live_project(tmp_path, monkeypatch, args=["w"])
    result = _live_run(root, policy, "w")   # fd 3 is /project/tools: ro is enforced by the nested OS profile
    assert "WRITE1" not in result["stdout"] and not (root / "tools" / "probe.out").exists()


@live
def test_live_timeout_kills_group(tmp_path, monkeypatch):
    root, policy = _live_project(tmp_path, monkeypatch, args=["spin"], limits={"timeout_s": 1})
    t0 = time.monotonic()
    result = _live_run(root, policy, "spin")
    assert result["exit"] != 0 and result["trap"] in ("timeout", "fuel") and time.monotonic() - t0 < 30
