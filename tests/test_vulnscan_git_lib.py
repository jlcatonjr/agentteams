"""scripts/vulnscan-remediation/vulnscan-git-lib.sh: hardened git for the vulnscan remediation scripts.

The scripts run unconfined, with the operator's credentials, in repositories whose git data a confined review agent
may have written. These tests plant hostile repository config and check that the wrappers neither run it nor
fetch through it, and that the exact allowlist of harmless keys (2026-11-10 scan of the VK checkouts) holds.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "scripts" / "vulnscan-remediation" / "vulnscan-git-lib.sh"
OWN = "https://github.com/Visual-Knowledge-LLC/collector-management.git/info/lfs"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                                reason="needs git and bash")


def _git(*args: str, cwd: Path | None = None) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(origin))
    work = tmp_path / "work"
    _git("init", "-q", str(work))
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i", cwd=work)
    _git("remote", "add", "origin", str(origin), cwd=work)
    _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=work)
    return work


def _bash(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PY": sys.executable, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["bash", "-c", f'. "{LIB}"\n{script}'], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=60)


def _refused(repo: Path, key: str, value: str) -> bool:
    _git("config", key, value, cwd=repo)
    try:
        return _bash('git_config_refusal "$PWD"', repo).returncode != 0
    finally:
        _git("config", "--unset-all", key, cwd=repo)


@pytest.mark.parametrize(("key", "value"), [
    ("http.postBuffer", "524288000"), ("http.lowSpeedTime", "600"), ("lfs.repositoryformatversion", "0"),
    (f"lfs.{OWN}.access", "basic"), (f"lfs.{OWN}.locksverify", "false"),
    ("filter.lfs.clean", "git-lfs clean -- %f"), ("filter.lfs.smudge", "git-lfs smudge -- %f"),
    ("filter.lfs.process", "git-lfs filter-process"), ("filter.lfs.required", "true"),
])
def test_known_harmless_keys_are_allowed(repo: Path, key: str, value: str) -> None:
    _git("remote", "set-url", "origin", "git@github.com:Visual-Knowledge-LLC/collector-management.git", cwd=repo)
    assert not _refused(repo, key, value)


@pytest.mark.parametrize(("key", "value"), [
    ("filter.lfs.smudge", "git-lfs smudge -- %f\ntouch pwned"),          # a newline must not hide a command
    ("http.postBuffer", "1\nx"), ("http.postBuffer", "99g"),
    ("http.proxy", "http://evil:8080"), ("http.https://github.com/.postBuffer", "1"),
    ("http.https://github.com/.sslVerify", "false"), ("http.extraHeader", "Authorization: x"),
    ("filter.lfs.process", "git-lfs filter-process; touch pwned"), ("filter.x.smudge", "cat"),
    ("lfs.url", "https://evil/lfs"), ("lfs.customtransfer.x.path", "/tmp/evil"),
    ("lfs.https://evil.example/x.git/info/lfs.access", "basic"), ("lfs.repositoryformatversion", "0; x"),
    ("core.worktree", "/tmp"), ("core.alternateRefsCommand", "x"), ("include.path", "/nonexistent/vulnscan-include"),
    ("url.https://evil/.insteadOf", "https://github.com/"), ("credential.helper", "!touch pwned"),
    ("remote.origin.proxy", "http://evil"), ("remote.origin.vcs", "evil"), ("gpg.program", "x"),
    ("pager.log", "touch pwned"),
])
def test_other_repo_local_keys_are_refused(repo: Path, key: str, value: str) -> None:
    _git("remote", "set-url", "origin", "git@github.com:Visual-Knowledge-LLC/collector-management.git", cwd=repo)
    assert _refused(repo, key, value)


def test_hostile_config_runs_nothing_and_unsafe_urls_are_refused(repo: Path, tmp_path: Path) -> None:
    pw = tmp_path / "pwned"
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text(f"#!/bin/sh\ntouch {pw}.hook\n")
    (hooks / "pre-commit").chmod(0o755)
    _git("config", "core.fsmonitor", f"touch {pw}.fsmonitor; false", cwd=repo)
    _git("config", "remote.origin.uploadpack", f"touch {pw}.uploadpack; git-upload-pack", cwd=repo)
    _git("config", "remote.origin.receivepack", f"touch {pw}.receivepack; git-receive-pack", cwd=repo)
    # fsmonitor, hooks and the pack programs are not refused: the flags and pinned pack programs neutralise them
    got = _bash('hgit -C "$PWD" status --porcelain >/dev/null && '
                'hgit -c user.email=t@t -c user.name=t -C "$PWD" commit -q --allow-empty -m x && '
                'hgit_net "$PWD" fetch -q origin main && hgit_net "$PWD" push -q origin HEAD:refs/heads/x && echo done',
                repo)
    assert got.stdout.strip() == "done", got.stderr
    assert not list(tmp_path.glob("pwned.*"))
    for url in ("ext::sh -c touch% /tmp/pwned.ext", "weird::x"):
        _git("remote", "set-url", "origin", url, cwd=repo)
        r = _bash('hgit_net "$PWD" fetch -q origin main', repo)
        assert r.returncode == 128 and "not an https, ssh or local path URL" in r.stderr
    _git("remote", "remove", "origin", cwd=repo)
    r = _bash('hgit_net "$PWD" fetch -q origin main', repo)
    assert r.returncode == 128 and "no readable URL" in r.stderr
    assert _bash('hgit_net "$PWD" pull', repo).returncode == 2


def test_an_unreadable_config_is_refused(repo: Path) -> None:
    with open(repo / ".git" / "config", "a") as f:
        f.write("[core\n\tbare = maybe\n")
    r = _bash('hgit -C "$PWD" status', repo)
    assert r.returncode == 128 and "could not be checked" in r.stderr


def test_a_crashing_config_parser_refuses_instead_of_reading_as_clean(repo: Path, tmp_path: Path) -> None:
    crash = tmp_path / "crashpy"
    crash.write_text("#!/bin/sh\ncat >/dev/null\nexit 1\n")   # exit 1 is also git config's "no key": must not mix
    crash.chmod(0o755)
    _git("config", "http.proxy", "http://evil", cwd=repo)
    env = {**os.environ, "PY": str(crash), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for script in ('git_config_refusal "$PWD"', 'git config --unset http.proxy; git_config_refusal "$PWD"'):
        r = subprocess.run(["bash", "-c", f'. "{LIB}"\n{script}'], cwd=repo, env=env, capture_output=True, text=True)
        assert r.returncode != 0 and "could not be checked" in r.stderr, r.stderr
