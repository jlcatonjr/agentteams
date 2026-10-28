"""Plan C2: the executor lets git run exactly git-lfs's standard filter config, and nothing else.

collector-management (2026-10-23) uses ``git lfs install``'s filter config, which ``fix.git_config_problem`` refused
as "a filter driver". A filter driver is a program git runs on checkout and status, so the allowlist is value for
value; git-lfs settings that name a program (extensions, custom transfer agents) refuse it again, and the executor's
git environment turns LFS smudging off.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams.vulnscan import executor, fix

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git not available")


def _repo(tmp_path: Path, **config: str) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    for key, value in config.items():
        subprocess.run(["git", "-C", str(repo), "config", key.replace("__", "."), value], check=True,
                       capture_output=True)
    return repo


LFS = {"filter__lfs__clean": "git-lfs clean -- %f", "filter__lfs__smudge": "git-lfs smudge -- %f",
       "filter__lfs__process": "git-lfs filter-process", "filter__lfs__required": "true"}


def test_the_standard_git_lfs_config_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fix, "lfs_available", lambda: True)
    assert fix.git_config_problem(_repo(tmp_path, **LFS)) == ""


def test_git_lfs_must_be_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fix, "lfs_available", lambda: False)
    assert "git-lfs is not installed" in fix.git_config_problem(_repo(tmp_path, **LFS))


@pytest.mark.parametrize("extra", [
    {"filter__lfs__clean": "git-lfs clean -- %f; touch /tmp/x"},      # a changed value
    {"filter__lfs__process": "sh -c 'touch /tmp/x'"},
    {"filter__other__smudge": "cat"},                                  # any other driver
])
def test_anything_else_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: dict) -> None:
    monkeypatch.setattr(fix, "lfs_available", lambda: True)
    assert "defines a filter driver" in fix.git_config_problem(_repo(tmp_path, **{**LFS, **extra}))


@pytest.mark.parametrize("key", ["lfs__extension__foo__clean", "lfs__customtransfer__bar__path",
                                 "lfs__customtransfer__bar__args", "lfs__standalonetransferagent",
                                 "lfs__https://lfs.example.invalid__standalonetransferagent", "lfs__storage"])
def test_git_lfs_settings_that_run_a_program_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    key: str) -> None:
    monkeypatch.setattr(fix, "lfs_available", lambda: True)
    assert "names a program or a storage path" in fix.git_config_problem(
        _repo(tmp_path, **{**LFS, key: "sh -c 'touch /tmp/x'"}))


def test_the_executor_git_environment_and_hardening() -> None:
    env = executor._clean_git_env("/dev/null")
    assert env["GIT_LFS_SKIP_SMUDGE"] == "1" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    flags = list(executor._GIT_HARDENING)
    assert "core.pager=cat" in flags and "core.hooksPath=/dev/null" in flags


def test_a_change_to_a_filtered_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    repo = _repo(tmp_path)
    (repo / ".gitattributes").write_text("requirements.txt filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
    (repo / "requirements-dev.txt").write_text("x==1\n", encoding="utf-8")
    executor._refuse_filtered(repo, ["requirements-dev.txt"])        # no filter attribute: fine
    with pytest.raises(executor.ExecError, match="requirements.txt has filter=lfs"):
        executor._refuse_filtered(repo, ["requirements-dev.txt", "requirements.txt"])


# ---------------------------------------------------------------- conditional includes (security follow-up to #311)

def _includes(tmp_path: Path, repo: Path, section: str, body: str) -> Path:
    """Write ``body`` to an include file and reference it from ``repo``'s config under ``section``."""
    inc = tmp_path / "inc.cfg"
    inc.write_text(body, encoding="utf-8")
    with (repo / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write(f'[{section}]\n\tpath = {inc}\n')
    return inc


@pytest.mark.parametrize("section", ['includeIf "gitdir:**/worktrees/**"', 'includeIf "onbranch:vulnscan/**"',
                                     'includeIf "hasconfig:remote.*.url:https://**"', 'IncludeIf "gitdir/i:**"'])
def test_a_conditional_include_is_refused(tmp_path: Path, section: str) -> None:
    # the driver it adds is invisible here and live in the executor's worktree or on its vulnscan/* branch
    repo = _repo(tmp_path)
    _includes(tmp_path, repo, section, '[filter "x"]\n\tsmudge = sh -c "touch /tmp/pwned"\n')
    assert "conditional include" in fix.git_config_problem(repo)


def test_a_conditional_include_nested_in_a_plain_include_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _includes(tmp_path, repo, "include", '[includeIf "gitdir:**/worktrees/**"]\n\tpath = /nonexistent\n')
    assert "conditional include" in fix.git_config_problem(repo)


def test_a_plain_include_is_checked_like_the_rest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    inc = _includes(tmp_path, repo, "include", "[core]\n\tautocrlf = false\n")
    assert fix.git_config_problem(repo) == ""
    inc.write_text('[filter "x"]\n\tsmudge = cat\n', encoding="utf-8")   # what it adds is seen here
    assert "defines a filter driver" in fix.git_config_problem(repo)


def test_stage_fix_rechecks_the_config_before_its_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    gits: list[tuple[str, ...]] = []

    def problem(directory: Path) -> str:
        calls.append(Path(directory))
        return "" if len(calls) == 1 else "the repository's git config has a conditional include (includeIf), x"

    def fake_git(repo: Path, *args: str, check: bool = True, push: bool = False) -> str:
        gits.append(args)
        if args[:2] == ("worktree", "add"):
            Path(args[-2]).mkdir(parents=True)
        return "0" * 40 if args[0] == "rev-parse" else ""

    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(fix, "git_config_problem", problem)
    monkeypatch.setattr(executor, "git", fake_git)
    monkeypatch.setattr(fix, "fix_path", lambda *a, **k: pytest.fail("fix_path ran in an unchecked worktree"))
    with pytest.raises(executor.ExecError, match="in the executor's worktree"):
        executor.stage_fix(tmp_path / "repo", {})
    assert len(calls) == 2 and calls[1].parent == executor._worktrees_dir()
    add = next(a for a in gits if a[:2] == ("worktree", "add"))
    assert "--no-checkout" in add                                   # nothing checked out before the recheck
    assert not any(a[0] in ("reset", "checkout") for a in gits)     # and nothing after the refusal


@pytest.mark.parametrize("which", [0, 1, 2])   # includeIf, filter drivers, git-lfs refused keys
def test_a_config_query_git_fails_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: int) -> None:
    repo = _repo(tmp_path, **LFS)
    real, seen = executor.query_git, []

    def flaky(directory: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        if args[:1] == ("config",):
            seen.append(args)
            if len(seen) == which + 1:
                return subprocess.CompletedProcess(["git", *args], 3, "", "fatal: bad config line 1")
        return real(directory, *args, timeout=timeout)

    monkeypatch.setattr(fix, "lfs_available", lambda: True)
    monkeypatch.setattr(executor, "query_git", flaky)
    assert "cannot read this repository's config" in fix.git_config_problem(repo)
