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
