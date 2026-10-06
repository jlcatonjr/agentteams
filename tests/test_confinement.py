"""P4b of the orchestrator-only-writes pilot: the runner's commands and gates run in an OS sandbox.

The live tests need a usable sandbox (``sandbox-exec`` on macOS, ``bwrap`` on Linux) and skip otherwise, for
example when the test process is itself already sandboxed. The CI ``macos-seatbelt`` job runs them.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import confinement as C
from agentteams import proposals as P
from agentteams.frameworks._write_roots import control_plane_of

PY = sys.executable
#: The interpreter's toolchain root, not just its bin dir: Homebrew's python3 re-executes Python.app inside its
#: framework (Versions/X.Y/Resources), which a bin-only allowlist refuses (found by these live tests).
PY_DIRS = sorted({os.path.dirname(os.path.dirname(os.path.realpath(PY))), os.path.dirname(PY)})
SANDBOX = C.available()
live = pytest.mark.skipif(SANDBOX is None, reason="no usable OS sandbox here")


# --- policy lint --------------------------------------------------------------------------------


@pytest.mark.parametrize("spec, needle", [
    ({"exec": ["python3"], "write": ["tmp"]}, "absolute"),
    ({"exec": [], "write": ["tmp"]}, "empty"),
    ({"exec": ["/usr/bin"], "write": ["."]}, "project root"),
    ({"exec": ["/usr/bin"], "write": [".agentteams"]}, "ledger"),
    ({"exec": ["/usr/bin"], "write": [".git/hooks"]}, "ledger"),
    ({"exec": ["/usr/bin"], "write": ["../x"]}, "project-relative"),
    ({"exec": ["/usr/bin"], "write": ["/abs"]}, "project-relative"),
    ({"exec": ["/usr/bin"], "extra": 1}, "exec, write"),
])
def test_confined_programs_lint(spec, needle):
    with pytest.raises(C.ConfinementError, match=needle):
        C.load_confined({"a": spec}, {}, control_plane_of)


def test_exec_path_inside_a_write_root_refused_whatever_the_cwd(project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)   # not the project: the check must resolve write roots against the project
    tool = project / "out" / "tool"
    policy = P.load_policy({
        "agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", "pass"], "args": []}]}},
        "confined_programs": {"x": {"exec": [*PY_DIRS, str(project / "out")], "write": ["out"]}}})
    del tool
    req = {"kind": "command-request", "dispatch": P.issue_dispatch(project, "x"), "argv": [PY, "-c", "pass"],
           "purpose": "t"}
    with pytest.raises(P.ProposalError, match="overlap"):
        P.run_request(req, root=project, policy=policy, confine=True)


def test_string_write_list_refused():
    with pytest.raises(C.ConfinementError, match="must be lists"):
        C.load_confined({"a": {"exec": ["/usr/bin"], "write": "out"}}, {}, control_plane_of)


def test_control_character_in_path_refused(tmp_path):
    with pytest.raises(C.ConfinementError, match="expressed safely"):
        C.seatbelt_profile(root=tmp_path, exec_paths=["/usr/bin\x01"], write_roots=[], tmp_dir=tmp_path)


def test_profile_shape(tmp_path):
    profile = C.seatbelt_profile(root=tmp_path, exec_paths=["/usr/bin"], write_roots=["out"], tmp_dir=tmp_path / "t")
    assert "(deny process-exec*)" in profile and '(subpath "/usr/bin")' in profile
    assert ".agentteams" in profile and ".git" in profile
    assert "(deny file-read* (subpath" in profile and "agentteams/keys" in profile


def test_unsafe_path_refused(tmp_path):
    with pytest.raises(C.ConfinementError, match="expressed safely"):
        C.seatbelt_profile(root=tmp_path, exec_paths=['/usr/bin"))(allow default'], write_roots=[],
                           tmp_dir=tmp_path)


def test_bwrap_argv_shape(tmp_path):
    (tmp_path / ".agentteams").mkdir()
    argv = C.bwrap_argv(root=tmp_path, write_roots=["out"], tmp_dir=tmp_path / "t", cwd=tmp_path)
    assert "--unshare-pid" in argv and "--die-with-parent" in argv
    i = argv.index(str(Path(os.path.realpath(tmp_path)) / ".agentteams"))
    assert argv[i - 1] == "--ro-bind"


# --- live confinement ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv(P.KEY_ENV[0], "test-key")
    root = tmp_path / "proj"
    (root / "out").mkdir(parents=True)
    (root / "out" / ".keep").write_text("")
    (root / "src").mkdir()
    (root / "src" / "a.txt").write_text("a\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    return root


def _policy(code: str, *, exec_paths=None, writes=("out/*",)) -> P.Policy:
    return P.load_policy({
        "agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": [], "writes": list(writes)}]}},
        "confined_programs": {"x": {"exec": exec_paths or PY_DIRS, "write": ["out"]}},
    })


def _run(root, code, **kw):
    policy = _policy(code, **kw)
    req = {"kind": "command-request", "dispatch": P.issue_dispatch(root, "x"), "argv": [PY, "-c", code],
           "purpose": "test", "expected_writes": ["out/r.txt"]}
    return P.run_request(req, root=root, policy=policy, confine=True)


@live
def test_declared_program_writes_its_root(project):
    result = _run(project, "open('out/r.txt', 'w').write('ok')")
    assert result["exit"] == 0 and (project / "out" / "r.txt").read_text() == "ok"


@live
@pytest.mark.parametrize("target", ["src/a.txt", ".agentteams/proposal-ledger.jsonl", "../escape.txt"])
def test_writes_outside_the_roots_are_refused_by_the_kernel(project, target):
    code = f"open({target!r}, 'w').write('x')"
    result = _run(project, code)
    assert result["exit"] != 0 and "Operation not permitted" in result["stderr"] + "PermissionError"
    assert P.verify_ledger(project) == []


@live
def test_key_directory_is_unreadable(project):
    keys = Path(os.path.expanduser(C.KEY_DIR))
    if not keys.is_dir() or not any(keys.iterdir()):
        pytest.skip("no operator key directory on this machine")
    code = f"import os; os.listdir({str(keys)!r})"
    assert _run(project, code)["exit"] != 0


@live
@pytest.mark.skipif(SANDBOX != "seatbelt", reason="program allowlist is macOS-only (decision C)")
def test_undeclared_program_refused(project):
    result = _run(project, "import subprocess; subprocess.run(['/bin/ls'], check=True)")
    assert result["exit"] != 0


def test_program_outside_exec_paths_refused_before_running(project):
    with pytest.raises(P.ProposalError, match="outside x's confined exec paths"):
        _run(project, "pass", exec_paths=["/nonexistent/bin"])


def test_agent_without_confined_entry_refused(project):
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", "pass"], "args": []}]}}})
    req = {"kind": "command-request", "dispatch": P.issue_dispatch(project, "x"), "argv": [PY, "-c", "pass"],
           "purpose": "t"}
    with pytest.raises(P.ProposalError, match="no confined_programs entry"):
        P.run_request(req, root=project, policy=policy, confine=True)


@live
def test_survivor_fails_the_run(project):
    # A child that detaches its output (as a daemon would) and keeps running after the command exits.
    code = ("import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'], "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)")
    with pytest.raises(P.UndeclaredWritesError, match="left processes running"):
        _run(project, code)


def test_no_sandbox_refuses_unless_opted_out(project, monkeypatch):
    monkeypatch.setattr(C, "available", lambda: None)
    with pytest.raises(P.ProposalError, match="no usable OS sandbox"):
        _run(project, "pass")
    policy = P.load_policy({
        "agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", "pass"], "args": []}]}},
        "confined_programs": {"x": {"exec": PY_DIRS, "write": ["out"]}}, "allow_unconfined_runs": True})
    req = {"kind": "command-request", "dispatch": P.issue_dispatch(project, "x"), "argv": [PY, "-c", "pass"],
           "purpose": "t"}
    assert P.run_request(req, root=project, policy=policy, confine=True)["exit"] == 0
    assert '"confined": "unconfined-opt-out"' in (project / P.LEDGER_REL).read_text()


@live
def test_gate_runs_confined(project):
    gate = project / "gate.py"   # inside the project: refused as a program, so use -c
    del gate
    policy = P.load_policy({
        "agent_policies": {"x": {"write_scopes": ["src/"]}},
        "proposal_gates": {"g": {"glob": "src/*.txt", "argv": [PY, "-c",
                                  "import sys; open('src/a.txt','w').write('pwned')", "{file}"],
                                  "exec": PY_DIRS}},
    })
    target = project / "src" / "a.txt"
    artifact = {"kind": "change-proposal", "dispatch": P.issue_dispatch(project, "x"), "path": "src/a.txt",
                "base_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "content": "b\n", "rationale": "t"}
    with pytest.raises(P.ProposalError, match="gate g refused"):
        P.apply_proposal(artifact, root=project, policy=policy, confine=True)
    assert target.read_text() == "a\n"   # the gate could not write the project


# --- concurrent dispatch use cap (P1 @security condition) ---------------------------------------


def _claim(args):
    root, nonce = args
    os.environ[P.KEY_ENV[0]] = "test-key"
    try:
        P.agent_for(Path(root), nonce)
        return True
    except P.ProposalError:
        return False


def test_concurrent_claims_never_exceed_max_uses(project):
    nonce = P.issue_dispatch(project, "x", max_uses=3)
    with multiprocessing.get_context("spawn").Pool(8) as pool:
        results = pool.map(_claim, [(str(project), nonce)] * 16)
    assert sum(results) == 3


# --- the P4b exit criterion: a real `lake build` inside the sandbox (local; needs elan) -------------

_ELAN = Path(os.path.expanduser("~/.elan"))
_TOOLCHAINS = sorted((_ELAN / "toolchains").glob("leanprover--lean4---v*")) if (_ELAN / "toolchains").is_dir() else []


@live
@pytest.mark.skipif(not _TOOLCHAINS or SANDBOX != "seatbelt", reason="needs elan with a Lean 4 toolchain, on macOS")
def test_lake_build_inside_the_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv(P.KEY_ENV[0], "test-key")
    root = tmp_path / "leanproj"
    root.mkdir()
    version = _TOOLCHAINS[-1].name.split("---")[-1]
    (root / "lean-toolchain").write_text(f"leanprover/lean4:{version}\n")
    (root / "lakefile.toml").write_text('name = "demo"\ndefaultTargets = ["Demo"]\n\n[[lean_lib]]\nname = "Demo"\n')
    (root / "Demo.lean").write_text("def x : Nat := 1\n")
    lake = str(_ELAN / "bin" / "lake")
    # A real project commits lake-manifest.json; create it once, unconfined, then start from a clean build.
    subprocess.run([lake, "build"], cwd=root, check=True, capture_output=True, timeout=300)
    subprocess.run([lake, "clean"], cwd=root, check=True, capture_output=True, timeout=60)
    (root / ".lake").mkdir(exist_ok=True)
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    policy = P.load_policy({
        "agent_policies": {"lean-prover": {"commands": [{"prefix": [lake, "build"], "args": [],
                                                         "writes": [".lake/**"]}]}},
        "confined_programs": {"lean-prover": {"exec": ["~/.elan"], "write": [".lake"]}},
    })
    req = {"kind": "command-request", "dispatch": P.issue_dispatch(root, "lean-prover"), "argv": [lake, "build"],
           "purpose": "build"}
    result = P.run_request(req, root=root, policy=policy, confine=True)
    assert result["exit"] == 0, result["stderr"]
    assert (root / ".lake" / "build" / "lib" / "lean" / "Demo.olean").exists()



def _req(project):
    return {"kind": "command-request", "dispatch": P.issue_dispatch(project, "x"), "argv": [PY, "-c", "pass"],
            "purpose": "t"}


def _confined_policy(spec, **extra):
    return P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", "pass"], "args": []}]}},
                          "confined_programs": {"x": spec}, **extra})


def test_symlinked_write_root_refused(project, tmp_path):
    (project / "out").rename(project / "out-real")
    (project / "out").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(P.ProposalError, match="symlink"):
        P.run_request(_req(project), root=project, policy=_confined_policy({"exec": PY_DIRS, "write": ["out"]}),
                      confine=True)


@pytest.mark.parametrize("broad", ["/", "~/"])
def test_too_broad_exec_path_refused(project, broad):
    with pytest.raises(P.ProposalError, match="too broad|outside"):
        P.run_request(_req(project), root=project, policy=_confined_policy({"exec": [broad, *PY_DIRS],
                                                                            "write": ["out"]}), confine=True)


def test_write_root_covering_a_protected_path_refused(project):
    policy = _confined_policy({"exec": PY_DIRS, "write": ["out"]}, protected_paths=["out/records.json"])
    with pytest.raises(P.ProposalError, match="protected path"):
        P.run_request(_req(project), root=project, policy=policy, confine=True)


def test_runner_refuses_an_environment_key(tmp_path, monkeypatch):
    from agentteams import proposal_runner as R
    monkeypatch.setenv(P.KEY_ENV[0], "leaky")
    (tmp_path / "brief.json").write_text('{"write_policy": "orchestrator-only"}')
    with pytest.raises(R.RunnerError, match="unset"):
        R.Runner(tmp_path, tmp_path / "brief.json", P.load_policy({}))


@live
@pytest.mark.skipif(SANDBOX != "seatbelt", reason="nested .git deny is a Seatbelt regex")
@pytest.mark.parametrize("name", [".git", ".GIT", ".Git"])
def test_nested_git_inside_a_write_root_is_write_denied(project, name):
    (project / "out" / "pkg" / name).mkdir(parents=True, exist_ok=True)
    result = _run(project, f"open('out/pkg/{name}/config', 'w').write('[core]')")
    assert result["exit"] != 0


def test_opt_out_does_not_exempt_writes_in_roots(project, monkeypatch):
    monkeypatch.setattr(C, "available", lambda: None)
    code = "open('out/x.txt', 'w').write('x')"
    policy = P.load_policy({
        "agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": []}]}},
        "confined_programs": {"x": {"exec": PY_DIRS, "write": ["out"]}}, "allow_unconfined_runs": True})
    req = {"kind": "command-request", "dispatch": P.issue_dispatch(project, "x"), "argv": [PY, "-c", code],
           "purpose": "t"}
    with pytest.raises(P.UndeclaredWritesError, match="out/x.txt"):
        P.run_request(req, root=project, policy=policy, confine=True)



@live
def test_glob_protected_file_inside_a_root_is_never_exempt(project):
    code = "open('out/records.json', 'w').write('{}')"
    policy = P.load_policy({
        "agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": []}]}},
        "confined_programs": {"x": {"exec": PY_DIRS, "write": ["out"]}}, "protected_paths": ["*.json"]})
    req = {"kind": "command-request", "dispatch": P.issue_dispatch(project, "x"), "argv": [PY, "-c", code],
           "purpose": "t"}
    with pytest.raises(P.UndeclaredWritesError, match="out/records.json"):
        P.run_request(req, root=project, policy=policy, confine=True)


def test_exec_path_containing_the_run_tmpdir_refused(project, tmp_path):
    tmp = tmp_path / "elsewhere" / "run"
    tmp.mkdir(parents=True)
    with pytest.raises(C.ConfinementError, match="overlap"):
        C.check_roots(project, [str(tmp_path / "elsewhere")], ["out"], tmp)


def test_project_ancestor_exec_path_refused(project):
    with pytest.raises(C.ConfinementError, match="too broad"):
        C.check_roots(project, [str(project.parent)], ["out"])



def test_wrap_uses_the_checked_absolute_roots(project, tmp_path):
    checked = C.check_roots(project, PY_DIRS, ["out"], tmp_path)
    assert checked == [os.path.realpath(project / "out")]
    profile = C.seatbelt_profile(root=project, exec_paths=PY_DIRS, write_roots=checked, tmp_dir=tmp_path)
    assert f'(subpath "{os.path.realpath(project / "out")}")' in profile



def test_brief_schema_accepts_the_p4b_fields():
    jsonschema = pytest.importorskip("jsonschema")
    import json
    schema = json.loads((Path(C.__file__).parent / "schemas" / "project-description.schema.json").read_text())
    for key, value in {
        "proposal_gates": {"g": {"glob": "*.lean", "argv": ["/usr/bin/true", "{file}"], "exec": ["/usr/bin"]}},
        "confined_programs": {"lean-prover": {"exec": ["~/.elan"], "write": [".lake"]}},
        "allow_unconfined_runs": False,
    }.items():
        jsonschema.validate(value, schema["properties"][key])
