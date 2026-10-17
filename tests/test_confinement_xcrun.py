"""mathAgents M6: xcrun launchers in the Seatbelt exec allowlist, and warnings for write roots over package stores."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from agentteams import confinement as C
from agentteams.cli.standalone_modes import xcrun_launcher_lines

CLT = "/Library/Developer/CommandLineTools"
GIT_DIRS = [f"{CLT}/usr/bin", f"{CLT}/usr/libexec/git-core"]


def fake_resolver(table: dict[str, list[str] | None], calls: list[str] | None = None) -> C.LauncherResolver:
    def resolve(name: str) -> list[str] | None:
        if calls is not None:
            calls.append(name)
        return table.get(name, [])
    return resolve


def broken(name: str) -> None:
    return None


# --- expansion helper ---------------------------------------------------------------------------------------------

def test_git_launcher_adds_tool_dir_and_exec_path() -> None:
    expanded, unresolved = C.expand_exec_paths(["/usr/bin/git", "/bin/sh"], fake_resolver({"git": GIT_DIRS}),
                                               platform="darwin")
    assert expanded == ["/usr/bin/git", "/bin/sh", *GIT_DIRS]
    assert unresolved == []


def test_non_launcher_adds_nothing() -> None:
    calls: list[str] = []
    expanded, unresolved = C.expand_exec_paths(["/usr/bin/true", "/opt/lean/bin"], fake_resolver({}, calls),
                                               platform="darwin")
    assert expanded == ["/usr/bin/true", "/opt/lean/bin"] and unresolved == []
    assert calls == ["true"]  # only /usr/bin programs are looked up


def test_usr_bin_itself_checks_every_candidate_and_dedupes() -> None:
    table = {"git": GIT_DIRS, "make": [f"{CLT}/usr/bin"], "xcodebuild": None}
    expanded, unresolved = C.expand_exec_paths(["/usr/bin"], fake_resolver(table), platform="darwin",
                                               usr_bin_names=lambda: ["git", "make", "true", "xcodebuild", "xcrun"])
    assert expanded == ["/usr/bin", *GIT_DIRS]
    assert unresolved == []  # an Xcode-only launcher missing on a CLT host fails unconfined too


def test_xcrun_failure_adds_nothing_and_notices(capsys: pytest.CaptureFixture[str]) -> None:
    expanded, unresolved = C.expand_exec_paths(["/usr/bin/git"], broken, platform="darwin")
    assert expanded == ["/usr/bin/git"] and unresolved == ["/usr/bin/git"]
    notice = C.unresolved_notice(unresolved)
    assert notice is not None and "\n" not in notice and "/usr/bin/git" in notice and "exit 71" in notice
    assert C.unresolved_notice([]) is None
    _, all_bad = C.expand_exec_paths(["/usr/bin"], broken, platform="darwin", usr_bin_names=lambda: ["git", "make"])
    assert all_bad == ["/usr/bin (its xcrun launchers)"]


def test_budget_exhausted_counts_as_unresolved() -> None:
    _, unresolved = C.expand_exec_paths(["/usr/bin/git"], fake_resolver({"git": GIT_DIRS}), platform="darwin",
                                        budget=-1)
    assert unresolved == ["/usr/bin/git"]


def test_linux_expansion_is_identity() -> None:
    calls: list[str] = []
    assert C.expand_exec_paths(["/usr/bin/git"], fake_resolver({"git": GIT_DIRS}, calls), platform="linux") == (
        ["/usr/bin/git"], [])
    assert calls == []


# --- profile and wrap ---------------------------------------------------------------------------------------------

def _profile(argv: list[str]) -> str:
    assert argv[:2] == ["/usr/bin/sandbox-exec", "-p"]
    return argv[2]


def test_wrapped_profile_contains_the_extra_subpaths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C.sys, "platform", "darwin")
    (tmp_path / "out").mkdir()
    argv = C.wrap(["/usr/bin/git", "status"], sandbox="seatbelt", root=tmp_path, cwd=tmp_path,
                  exec_paths=["/usr/bin/git"], write_roots=["out"], tmp_dir=tmp_path / "out",
                  resolver=fake_resolver({"git": GIT_DIRS}))
    profile = _profile(argv)
    allow = profile.split("(allow process-exec*", 1)[1]
    for path in ["/usr/bin/git", *GIT_DIRS]:
        assert f'(subpath "{os.path.realpath(path)}")' in allow
    assert argv[3:] == ["/usr/bin/git", "status"]


def test_wrap_drops_an_extra_that_overlaps_a_write_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C.sys, "platform", "darwin")
    planted = tmp_path / "out" / "bin"
    planted.mkdir(parents=True)
    argv = C.wrap(["/usr/bin/git"], sandbox="seatbelt", root=tmp_path, cwd=tmp_path, exec_paths=["/usr/bin/git"],
                  write_roots=["out"], tmp_dir=tmp_path, resolver=fake_resolver({"git": [str(planted), "/"]}))
    allow = _profile(argv).split("(allow process-exec*", 1)[1]
    assert str(os.path.realpath(planted)) not in allow and '(subpath "/")' not in allow


def test_wrap_prints_the_notice_when_xcrun_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(C.sys, "platform", "darwin")
    argv = C.wrap(["/usr/bin/git"], sandbox="seatbelt", root=tmp_path, cwd=tmp_path, exec_paths=["/usr/bin/git"],
                  write_roots=[], tmp_dir=tmp_path, resolver=broken)
    assert C.LAUNCHER_WRAPPERS and "libexec" not in _profile(argv)
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "/usr/bin/git" in err[0] and "exit 71" in err[0]


def test_seatbelt_profile_itself_is_unchanged(tmp_path: Path) -> None:
    profile = C.seatbelt_profile(root=tmp_path, exec_paths=["/usr/bin/git"], write_roots=[], tmp_dir=tmp_path)
    assert "libexec" not in profile  # pure: expansion happens in wrap, not here


def test_bwrap_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    argv = C.wrap(["/usr/bin/git"], sandbox="bwrap", root=tmp_path, cwd=tmp_path, exec_paths=["/usr/bin/git"],
                  write_roots=[], tmp_dir=tmp_path, resolver=fake_resolver({"git": GIT_DIRS}, calls))
    assert calls == [] and not any("libexec" in a for a in argv)
    assert argv == [*C.bwrap_argv(root=tmp_path, write_roots=[], tmp_dir=tmp_path, cwd=tmp_path), "--", "/usr/bin/git"]


# --- runner warning and --check-wiring ----------------------------------------------------------------------------

def test_launcher_warning_for_program_and_lake_wrapper() -> None:
    w = C.launcher_warning("/usr/bin/git", ["/usr/bin/git"], broken, platform="darwin")
    assert w is not None and w.startswith("WARNING:") and "exit 71" in w
    lake = C.launcher_warning("/opt/example/.elan/bin/lake", ["/opt/example/.elan", "/usr/bin/git"], broken, platform="darwin")
    assert lake is not None and "/usr/bin/git" in lake and "lake" in lake
    assert C.launcher_warning("/opt/example/.elan/bin/lake", ["/opt/example/.elan"], broken, platform="darwin") is None
    assert C.launcher_warning("/usr/bin/git", ["/usr/bin/git"], fake_resolver({"git": GIT_DIRS}),
                              platform="darwin") is None
    assert C.launcher_warning("/usr/bin/git", ["/usr/bin/git"], broken, platform="linux") is None


def test_check_wiring_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "read_confined_file", lambda root: None)
    brief = {"confined_programs": {"lean": {"exec": ["/usr/bin/git", "/opt/lean"], "write": ["lean/.lake/build"]}},
             "proposal_gates": {"lint": {"argv": ["/usr/bin/make", "lint"]}, "py": {"argv": ["python3"]}}}
    ok = xcrun_launcher_lines(tmp_path, brief, fake_resolver({"git": GIT_DIRS, "make": [f"{CLT}/usr/bin"]}),
                              platform="darwin")
    assert ok == [f"confined_programs.lean: xcrun launchers reach their real tools; also allowed: {', '.join(GIT_DIRS)}",
                  f"proposal_gates.lint: xcrun launchers reach their real tools; also allowed: {CLT}/usr/bin"]
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
def test_real_xcrun_resolves_git() -> None:
    resolver = C.xcrun_resolver()
    extra = resolver("git")
    if extra is None:
        pytest.skip("xcrun is present but no developer tools are installed")
    assert extra and all(not d.startswith("/usr/bin") for d in extra)
    assert len(extra) == 2 and extra[1].endswith("git-core")
    assert shutil.which("git", path=extra[0])
    assert resolver("true") == []  # a plain /usr/bin program is not a launcher


# --- the runner: once per run, with the injected resolver ---------------------------------------------------------

@pytest.fixture
def runner_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import subprocess

    from agentteams import proposals as P

    monkeypatch.setenv(P.KEY_ENV[0], "test-key-0123456789abcdef0123456")
    root = tmp_path / "proj"
    (root / "lean" / ".lake" / "packages" / "mathlib").mkdir(parents=True)
    (root / "lean" / ".lake" / "packages" / "mathlib" / ".keep").write_text("")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def test_runner_warns_once_for_lake_over_an_unresolved_git(runner_project: Path, tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    from agentteams import proposals as P

    py = sys.executable
    py_dirs = sorted({os.path.dirname(os.path.dirname(os.path.realpath(py))), os.path.dirname(py)})
    lake = tmp_path / "bin" / "lake"  # a stand-in named like the wrapper, outside the project
    lake.parent.mkdir()
    lake.symlink_to(os.path.realpath(py))
    calls: list[str] = []
    resolvers: list[C.LauncherResolver] = []

    def make_resolver(timeout: float = C.XCRUN_TIMEOUT) -> C.LauncherResolver:
        resolvers.append(fake_resolver({"git": None}, calls))
        return resolvers[-1]

    seen: dict[str, object] = {}

    def fake_wrap(argv: list[str], **kw: object) -> list[str]:
        seen.update(kw)
        return argv

    monkeypatch.setattr(C.sys, "platform", "darwin")
    monkeypatch.setattr(C, "available", lambda: "seatbelt")
    monkeypatch.setattr(C, "xcrun_resolver", make_resolver)
    monkeypatch.setattr(C, "wrap", fake_wrap)
    policy = P.load_policy({
        "agent_policies": {"x": {"commands": [{"prefix": [str(lake), "-c", "pass"], "args": []}]}},
        "confined_programs": {"x": {"exec": [*py_dirs, "/usr/bin/git"], "write": ["lean"]}},
    })
    req = {"kind": "command-request", "dispatch": P.issue_dispatch(runner_project, "x"),
           "argv": [str(lake), "-c", "pass"], "purpose": "t"}
    assert P.run_request(req, root=runner_project, policy=policy, confine=True)["exit"] == 0
    err = [line for line in capsys.readouterr().err.splitlines() if line.startswith("[runner]")]
    assert len(err) == 2
    assert "package store 'lean/.lake/packages'" in err[0]
    assert err[1].startswith("[runner] WARNING: /usr/bin/git") and "exit 71" in err[1]
    assert len(resolvers) == 1 and seen["resolver"] is resolvers[0]  # one resolver per run, shared with wrap
