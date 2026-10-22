"""mathAgents M6: xcrun launchers in the Seatbelt exec allowlist, and warnings for write roots over package stores."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agentteams import confinement as C
from agentteams.cli.standalone_modes import xcrun_launcher_lines

CLT = "/Library/Developer/CommandLineTools"
GIT = f"{CLT}/usr/bin/git"
GIT_CORE = f"{CLT}/usr/libexec/git-core"
GIT_ENTRIES = [("literal", GIT), ("subpath", GIT_CORE)]


def fake_stat(uid: int = 0, mode: int = 0o40755) -> os.stat_result:
    return os.stat_result((mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))


def lstat_table(bad: dict[str, os.stat_result] | None = None) -> C.Callable[[str], os.stat_result]:
    """Every path root-owned 0755 unless listed in *bad*."""
    return lambda path: (bad or {}).get(path, fake_stat())


@pytest.fixture(autouse=True)
def _root_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "_lstat", lstat_table())  # hosts without the CLT paths still see them as root-owned


def fake_resolver(table: dict[str, list[C.ExecEntry] | None], calls: list[str] | None = None) -> C.LauncherResolver:
    def resolve(name: str) -> list[C.ExecEntry] | None:
        if calls is not None:
            calls.append(name)
        return table.get(name, [])
    return resolve


def broken(name: str) -> None:
    return None


# --- expansion helper ---------------------------------------------------------------------------------------------

def test_named_git_adds_the_real_tool_literal_and_its_exec_path() -> None:
    exp = C.expand_exec_paths(["/usr/bin/git", "/bin/sh"], fake_resolver({"git": GIT_ENTRIES}), platform="darwin")
    assert exp.extras == GIT_ENTRIES  # never the whole CLT bin directory
    assert exp.unresolved == [] and exp.dropped == []


def test_non_launcher_adds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # The expansion is macOS's, so the host's /usr/bin must not leak in: Ubuntu 26's Rust coreutils make /usr/bin/true
    # a link to gnutrue (#262). Resolve /usr/bin paths as a macOS host would (no links there); everything else is
    # unchanged. (A temp symlink can't stand in: the expansion keys on the literal /usr/bin directory.)
    real = C.os.path.realpath
    monkeypatch.setattr(C.os.path, "realpath", lambda p: p if str(p).startswith("/usr/bin/") else real(p))
    calls: list[str] = []
    exp = C.expand_exec_paths(["/usr/bin/true", "/opt/lean/bin"], fake_resolver({}, calls), platform="darwin")
    assert exp == C.Expansion([], [], [])
    assert calls == ["true"]  # only /usr/bin programs are looked up


def test_usr_bin_itself_allows_tool_directories_and_dedupes() -> None:
    table = {"git": GIT_ENTRIES, "make": [("literal", f"{CLT}/usr/bin/make")], "xcodebuild": None}
    exp = C.expand_exec_paths(["/usr/bin"], fake_resolver(table), platform="darwin",
                              usr_bin_names=lambda: ["git", "make", "true", "xcodebuild", "xcrun"])
    assert exp.extras == [("subpath", f"{CLT}/usr/bin"), ("subpath", GIT_CORE)]
    assert exp.unresolved == []  # an Xcode-only launcher missing on a CLT host fails unconfined too


def test_named_program_stays_literal_even_with_usr_bin_declared() -> None:
    exp = C.expand_exec_paths(["/usr/bin/git", "/usr/bin"], fake_resolver({"git": GIT_ENTRIES}), platform="darwin",
                              usr_bin_names=lambda: ["git"])
    assert exp.extras == GIT_ENTRIES


def test_xcrun_failure_adds_nothing_and_notices() -> None:
    exp = C.expand_exec_paths(["/usr/bin/git"], broken, platform="darwin")
    assert exp.extras == [] and exp.unresolved == ["/usr/bin/git"]
    [notice] = C.expansion_notices(exp)
    assert "\n" not in notice and "/usr/bin/git" in notice and "exit 71" in notice
    assert C.expansion_notices(C.Expansion([], [], [])) == []
    all_bad = C.expand_exec_paths(["/usr/bin"], broken, platform="darwin", usr_bin_names=lambda: ["git", "make"])
    assert all_bad.unresolved == ["/usr/bin (its xcrun launchers)"]


def test_budget_exhausted_counts_as_unresolved() -> None:
    exp = C.expand_exec_paths(["/usr/bin/git"], fake_resolver({"git": GIT_ENTRIES}), platform="darwin", budget=-1)
    assert exp.unresolved == ["/usr/bin/git"]


def test_linux_expansion_adds_nothing() -> None:
    calls: list[str] = []
    assert C.expand_exec_paths(["/usr/bin/git"], fake_resolver({"git": GIT_ENTRIES}, calls),
                               platform="linux") == C.Expansion([], [], [])
    assert calls == []


# --- ownership check ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad_path, st, reason", [
    (GIT, fake_stat(uid=501, mode=0o100755), "not owned by root"),
    (f"{CLT}/usr", fake_stat(mode=0o40775), "writable by group or others"),
    ("/Library", fake_stat(mode=0o40757), "writable by group or others"),
])
def test_ownership_check_drops_and_notices(bad_path: str, st: os.stat_result, reason: str) -> None:
    lstat = lstat_table({bad_path: st})
    assert reason in (C.ownership_problem(GIT, lstat) or "") and bad_path in (C.ownership_problem(GIT, lstat) or "")
    exp = C.expand_exec_paths(["/usr/bin/git"], fake_resolver({"git": GIT_ENTRIES}), platform="darwin", lstat=lstat)
    assert ("literal", GIT) not in exp.extras and exp.unresolved == ["/usr/bin/git"]
    assert any(GIT in d and reason in d for d in exp.dropped)
    assert any("not allowing" in n and reason in n for n in C.expansion_notices(exp))


def test_ownership_check_for_a_user_owned_developer_dir() -> None:
    home_xcode = "/Users/example/Applications/Xcode.app/Contents/Developer"
    entries = [("literal", f"{home_xcode}/usr/bin/git"), ("subpath", f"{home_xcode}/usr/libexec/git-core")]
    lstat = lstat_table({"/Users/example": fake_stat(uid=501)})
    exp = C.expand_exec_paths(["/usr/bin/git"], fake_resolver({"git": entries}), platform="darwin", lstat=lstat)
    assert exp.extras == [] and len(exp.dropped) == 2 and exp.unresolved == ["/usr/bin/git"]


def test_ownership_check_passes_root_owned_chain() -> None:
    assert C.ownership_problem(GIT, lstat_table()) is None


def test_ownership_check_unreadable_path() -> None:
    def lstat(path: str) -> os.stat_result:
        raise FileNotFoundError(2, "No such file or directory")
    assert "can't be checked" in (C.ownership_problem(GIT, lstat) or "")


# --- profile and wrap ---------------------------------------------------------------------------------------------

def _allow(argv: list[str]) -> str:
    assert argv[:2] == ["/usr/bin/sandbox-exec", "-p"]
    return argv[2].split("(allow process-exec*", 1)[1]


def test_wrapped_profile_has_literal_tool_and_subpath_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C.sys, "platform", "darwin")
    (tmp_path / "out").mkdir()
    argv = C.wrap(["/usr/bin/git", "status"], sandbox="seatbelt", root=tmp_path, cwd=tmp_path,
                  exec_paths=["/usr/bin/git"], write_roots=["out"], tmp_dir=tmp_path / "out",
                  resolver=fake_resolver({"git": GIT_ENTRIES}))
    allow = _allow(argv)
    assert f'(subpath "{os.path.realpath("/usr/bin/git")}")' in allow
    assert f'(literal "{os.path.realpath(GIT)}")' in allow
    assert f'(subpath "{os.path.realpath(GIT_CORE)}")' in allow
    assert f'(subpath "{os.path.realpath(CLT + "/usr/bin")}")' not in allow
    assert argv[3:] == ["/usr/bin/git", "status"]


def test_seatbelt_profile_emits_literals(tmp_path: Path) -> None:
    profile = C.seatbelt_profile(root=tmp_path, exec_paths=["/opt/a"], write_roots=[], tmp_dir=tmp_path,
                                 exec_literals=["/opt/b/tool"])
    allow = profile.split("(allow process-exec*", 1)[1]
    assert '(subpath "/opt/a")' in allow and '(literal "/opt/b/tool")' in allow


def test_wrap_drops_an_extra_that_overlaps_a_write_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C.sys, "platform", "darwin")
    planted = tmp_path / "out" / "bin"
    planted.mkdir(parents=True)
    argv = C.wrap(["/usr/bin/git"], sandbox="seatbelt", root=tmp_path, cwd=tmp_path, exec_paths=["/usr/bin/git"],
                  write_roots=["out"], tmp_dir=tmp_path,
                  resolver=fake_resolver({"git": [("literal", str(planted / "git")), ("subpath", "/")]}))
    allow = _allow(argv)
    assert str(os.path.realpath(planted)) not in allow and '(subpath "/")' not in allow


def test_wrap_prints_the_notice_when_xcrun_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(C.sys, "platform", "darwin")
    argv = C.wrap(["/usr/bin/git"], sandbox="seatbelt", root=tmp_path, cwd=tmp_path, exec_paths=["/usr/bin/git"],
                  write_roots=[], tmp_dir=tmp_path, resolver=broken)
    assert "libexec" not in _allow(argv)
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "/usr/bin/git" in err[0] and "exit 71" in err[0]


def test_seatbelt_profile_itself_is_unchanged(tmp_path: Path) -> None:
    profile = C.seatbelt_profile(root=tmp_path, exec_paths=["/usr/bin/git"], write_roots=[], tmp_dir=tmp_path)
    assert "libexec" not in profile and "(literal \"/usr/bin/git" not in profile


def test_bwrap_is_unchanged(tmp_path: Path) -> None:
    calls: list[str] = []
    argv = C.wrap(["/usr/bin/git"], sandbox="bwrap", root=tmp_path, cwd=tmp_path, exec_paths=["/usr/bin/git"],
                  write_roots=[], tmp_dir=tmp_path, resolver=fake_resolver({"git": GIT_ENTRIES}, calls))
    assert calls == [] and not any("libexec" in a for a in argv)
    assert argv == [*C.bwrap_argv(root=tmp_path, write_roots=[], tmp_dir=tmp_path, cwd=tmp_path), "--", "/usr/bin/git"]


def test_real_resolver_runs_from_root(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[list[str], object]] = []

    def fake_run(argv: list[str], **kw: object) -> C.subprocess.CompletedProcess[str]:
        seen.append((argv, kw.get("cwd")))
        out = {"/usr/bin/xcode-select": CLT, "/usr/bin/xcrun": GIT}.get(argv[0], GIT_CORE)
        return C.subprocess.CompletedProcess(argv, 0, out + "\n", "")

    monkeypatch.setattr(C.os.path, "exists", lambda p: True)
    monkeypatch.setattr(C.os.path, "realpath", lambda p: p)
    monkeypatch.setattr(C.subprocess, "run", fake_run)
    assert C.xcrun_resolver()("git") == GIT_ENTRIES
    assert [a[0] for a, _ in seen] == ["/usr/bin/xcode-select", "/usr/bin/xcrun", GIT]
    assert all(cwd == "/" for _, cwd in seen)


# --- runner warning and --check-wiring ----------------------------------------------------------------------------

def test_unresolved_launchers_for_program_and_lake_wrapper() -> None:
    assert C.unresolved_launchers("/usr/bin/git", ["/usr/bin/git"], broken, platform="darwin") == ["/usr/bin/git"]
    lake = "/opt/example/.elan/bin/lake"
    assert C.unresolved_launchers(lake, ["/opt/example/.elan", "/usr/bin/git"], broken,
                                  platform="darwin") == ["/usr/bin/git"]
    assert C.unresolved_launchers(lake, ["/opt/example/.elan"], broken, platform="darwin") == []
    assert C.unresolved_launchers("/usr/bin/git", ["/usr/bin/git"], fake_resolver({"git": GIT_ENTRIES}),
                                  platform="darwin") == []
    assert C.unresolved_launchers("/usr/bin/git", ["/usr/bin/git"], broken, platform="linux") == []
    w = C.launcher_warning(lake, ["/usr/bin/git"])
    assert w is not None and w.startswith("WARNING: /usr/bin/git") and "exit 71" in w and lake in w
    assert C.launcher_warning(lake, []) is None


def test_check_wiring_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "read_confined_file", lambda root: None)
    brief = {"confined_programs": {"lean": {"exec": ["/usr/bin/git", "/opt/lean"], "write": ["lean/.lake/build"]}},
             "proposal_gates": {"lint": {"argv": ["/usr/bin/make", "lint"]}, "py": {"argv": ["python3"]}}}
    make = [("literal", f"{CLT}/usr/bin/make")]
    ok = xcrun_launcher_lines(tmp_path, brief, fake_resolver({"git": GIT_ENTRIES, "make": make}), platform="darwin")
    assert ok == [f"confined_programs.lean: xcrun launchers reach their real tools; also allowed: literal {GIT}, "
                  f"subpath {GIT_CORE}",
                  f"proposal_gates.lint: xcrun launchers reach their real tools; also allowed: literal {CLT}/usr/bin/make"]
    bad = xcrun_launcher_lines(tmp_path, brief, broken, platform="darwin")
    assert [b.split(":")[0] for b in bad] == ["WARNING", "WARNING"]
    assert "/usr/bin/git" in bad[0] and "exit 71" in bad[0]
    assert xcrun_launcher_lines(tmp_path, brief, broken, platform="linux") == []


# --- package stores -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("store", C.PACKAGE_STORES)
def test_package_store_warning_fires_for_each_pattern(store: str) -> None:
    for rel in (store, f"proj/{store}", f"proj/{store}/sub"):  # equals, equals deeper, inside
        [warning] = C.package_store_warnings([rel])
        assert repr(rel) in warning and "delete and re-create" in warning and "build outputs" in warning


@pytest.mark.parametrize("store", C.PACKAGE_STORES)
def test_package_store_warning_when_root_contains_a_store(tmp_path: Path, store: str) -> None:
    (tmp_path / "proj" / "a" / store).mkdir(parents=True)
    [warning] = C.package_store_warnings(["proj"], tmp_path)
    assert "'proj'" in warning and store in warning
    assert C.package_store_warnings(["proj"]) == []  # path-only: containment needs the tree


def test_partial_store_prefix_counts_as_containing() -> None:
    assert C.package_store_warnings(["lean/.lake"]) and C.package_store_warnings([".cargo"])


def test_package_store_silent_for_build_outputs(tmp_path: Path) -> None:
    (tmp_path / "lean" / ".lake" / "build" / "lib").mkdir(parents=True)
    (tmp_path / "lean" / ".lake" / "packages" / "mathlib").mkdir(parents=True)
    assert C.package_store_warnings(["lean/.lake/build", "src", "venvs-docs"], tmp_path) == []
    [warning] = C.package_store_warnings(["lean"], tmp_path)  # the incident's shape: a root over .lake/packages
    assert "'lean/.lake/packages'" in warning


def test_package_store_walk_skips_symlinks(tmp_path: Path) -> None:
    (tmp_path / "elsewhere" / "node_modules").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "link").symlink_to(tmp_path / "elsewhere")
    assert C.package_store_warnings(["out"], tmp_path) == []


# --- live (this macOS host) ---------------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists("/usr/bin/xcrun"),
                    reason="needs macOS with /usr/bin/xcrun")
def test_real_xcrun_resolves_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "_lstat", os.lstat)
    resolver = C.xcrun_resolver()
    entries = resolver("git")
    if entries is None:
        pytest.skip("xcrun is present but no developer tools are installed")
    [(k1, tool), (k2, helper)] = entries
    assert (k1, k2) == ("literal", "subpath") and not tool.startswith("/usr/bin/")
    assert os.path.basename(tool) == "git" and helper.endswith("git-core") and os.access(tool, os.X_OK)
    assert resolver("true") == []  # a plain /usr/bin program is not a launcher
    exp = C.expand_exec_paths(["/usr/bin/git"], resolver)
    # Whether the developer directory is root-owned depends on the host: a Command Line Tools install is; a CI
    # runner's Xcode may not be. Either way the ownership rule must hold exactly.
    if all(C.ownership_problem(path) is None for _, path in entries):
        assert exp.extras == entries and exp.dropped == []
    else:
        assert exp.extras == [] and exp.dropped and exp.unresolved == ["/usr/bin/git"]


# --- the runner: warn once per run; refuse the M6 combination unless opted in --------------------------------------

@pytest.fixture
def runner_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import subprocess

    from agentteams import proposals as P

    monkeypatch.setenv(P.KEY_ENV[0], "test-key-0123456789abcdef0123456")
    root = tmp_path / "proj"
    (root / "lean" / ".lake" / "packages" / "mathlib").mkdir(parents=True)
    (root / "lean" / ".lake" / "packages" / "mathlib" / ".keep").write_text("")
    (root / "out").mkdir()
    (root / "out" / ".keep").write_text("")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def _run_lake(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, git: list[C.ExecEntry] | None,
              write: str, opt_in: bool = False) -> tuple[dict[str, object], dict[str, object], list]:
    from agentteams import proposals as P

    py = sys.executable
    py_dirs = sorted({os.path.dirname(os.path.dirname(os.path.realpath(py))), os.path.dirname(py)})
    lake = tmp_path / "bin" / "lake"  # a stand-in named like the wrapper, outside the project
    lake.parent.mkdir(exist_ok=True)
    if not lake.exists():
        lake.symlink_to(os.path.realpath(py))
    resolvers: list[C.LauncherResolver] = []

    def make_resolver(timeout: float = C.XCRUN_TIMEOUT) -> C.LauncherResolver:
        resolvers.append(fake_resolver({"git": git}))
        return resolvers[-1]

    seen: dict[str, object] = {}

    def fake_wrap(argv: list[str], **kw: object) -> list[str]:
        seen.update(kw)
        return argv

    monkeypatch.setattr(C.sys, "platform", "darwin")
    monkeypatch.setattr(C, "available", lambda: "seatbelt")
    monkeypatch.setattr(C, "xcrun_resolver", make_resolver)
    monkeypatch.setattr(C, "wrap", fake_wrap)
    jail: dict[str, object] = {"exec": [*py_dirs, "/usr/bin/git"], "write": [write]}
    if opt_in:
        jail[C.OPT_IN_LAUNCHER_STORE] = True
    policy = P.load_policy({
        "agent_policies": {"x": {"commands": [{"prefix": [str(lake), "-c", "pass"], "args": []}]}},
        "confined_programs": {"x": jail},
    })
    req = {"kind": "command-request", "dispatch": P.issue_dispatch(root, "x"),
           "argv": [str(lake), "-c", "pass"], "purpose": "t"}
    return P.run_request(req, root=root, policy=policy, confine=True), seen, resolvers


def _runner_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [line for line in capsys.readouterr().err.splitlines() if line.startswith("[runner]")]


def test_runner_refuses_unresolved_launcher_plus_package_store(runner_project: Path, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams import proposals as P

    with pytest.raises(P.ProposalError) as info:
        _run_lake(runner_project, tmp_path, monkeypatch, git=None, write="lean")
    msg = str(info.value)
    assert "/usr/bin/git" in msg and "lean/.lake/packages" in msg and C.OPT_IN_LAUNCHER_STORE in msg
    ledger = runner_project / P.LEDGER_REL
    assert not ledger.exists() or "run-request-start" not in ledger.read_text()  # refused before anything ran


def test_runner_opt_in_runs_and_warns_once(runner_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    result, seen, resolvers = _run_lake(runner_project, tmp_path, monkeypatch, git=None, write="lean", opt_in=True)
    assert result["exit"] == 0
    lines = _runner_lines(capsys)
    assert len(lines) == 2 and "package store 'lean/.lake/packages'" in lines[0]
    assert lines[1].startswith("[runner] WARNING: /usr/bin/git") and "exit 71" in lines[1]
    assert len(resolvers) == 1 and seen["resolver"] is resolvers[0]  # one resolver per run, shared with wrap


def test_runner_unresolved_launcher_alone_only_warns(runner_project: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    result, _, _ = _run_lake(runner_project, tmp_path, monkeypatch, git=None, write="out")
    assert result["exit"] == 0
    [line] = _runner_lines(capsys)
    assert "WARNING: /usr/bin/git" in line


def test_runner_package_store_alone_only_warns(runner_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    result, _, _ = _run_lake(runner_project, tmp_path, monkeypatch, git=GIT_ENTRIES, write="lean")
    assert result["exit"] == 0
    [line] = _runner_lines(capsys)
    assert "package store" in line


def test_opt_in_must_be_boolean() -> None:
    from agentteams.frameworks._write_roots import control_plane_of

    with pytest.raises(C.ConfinementError, match=C.OPT_IN_LAUNCHER_STORE):
        C.load_confined({"x": {"exec": ["/usr/bin"], "write": ["out"], C.OPT_IN_LAUNCHER_STORE: "yes"}}, {},
                        control_plane_of)
    ok = C.load_confined({"x": {"exec": ["/usr/bin"], "write": ["out"], C.OPT_IN_LAUNCHER_STORE: True}}, {},
                         control_plane_of)
    assert ok["x"][C.OPT_IN_LAUNCHER_STORE] is True
    assert C.OPT_IN_LAUNCHER_STORE not in C.load_confined({"x": {"exec": ["/usr/bin"], "write": ["out"]}}, {},
                                                         control_plane_of)["x"]
