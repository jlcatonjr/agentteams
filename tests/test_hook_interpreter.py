"""The pre-commit hook resolves its own interpreter and records a skip (#447 a, and the (c) locked-install ask).

The hook used to test PATH's ``python3`` and, on a 3.9 interpreter, let the commit through with one printed line.
Now it uses the locked installation in a release-locked repository, otherwise the recorded interpreter or a 3.11+
``python3.x`` on PATH outside the working tree, and records every skip in a marker that ``api_impact status``
reports (exit 1) until a hook run that reaches the API impact step clears it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from agentteams import _hook_python, api_impact, git_hooks

REPO_ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("git") is None or sys.version_info < (3, 11),
                                reason="git and Python 3.11+")
_BASE_ENV = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
_BASE_ENV.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                  "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull,
                  "GIT_CONFIG_NOSYSTEM": "1"})
#: Commands the hook and git need, linked into one directory so PATH holds no Python at all. A directory such as
#: /usr/bin cannot be put on PATH: on a GitHub-hosted runner it holds real python3.1x interpreters.
_TOOLS = ("git", "sh", "grep", "head", "date", "readlink", "rm", "awk", "sed", "cat", "tr", "touch", "mkdir",
          "dirname", "basename", "env", "uname")
_TOOL_DIR: Path | None = None


def _tool_dir() -> Path:
    global _TOOL_DIR
    if _TOOL_DIR is None:
        _TOOL_DIR = Path(tempfile.mkdtemp(prefix="hook-tools-"))
        for name in _TOOLS:
            found = shutil.which(name)
            if found:
                (_TOOL_DIR / name).symlink_to(Path(found).resolve() if name == "git" else found)
    return _TOOL_DIR
_SKIPPED = "hooks SKIPPED, API impact not checked"


def _fake_bin(tmp_path: Path) -> Path:
    """A PATH directory whose only python3 is a 3.9 stand-in (it fails every version probe and every run)."""
    d = tmp_path / "bin"
    d.mkdir()
    py = d / "python3"
    py.write_text("#!/bin/sh\necho 'Python 3.9.6 stand-in' >&2\nexit 1\n")
    py.chmod(0o755)
    return d


def _env(path_dirs: list[Path]) -> dict[str, str]:
    # git and the core utilities stay reachable; no real python is on PATH.
    return {**_BASE_ENV, "PATH": os.pathsep.join([*(str(p) for p in path_dirs), str(_tool_dir())])}


def _run(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, text=True, check=False)


def _repo(tmp_path: Path, env: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, env, "init", "-q", "-b", "main")
    (repo / "mod.py").write_text("def a():\n    pass\n")
    _run(repo, env, "add", "-A")
    assert _run(repo, env, "commit", "-q", "--no-verify", "-m", "base").returncode == 0
    return repo


def _install(repo: Path, recorded: str) -> None:
    block = git_hooks._render_env_guard_block() + git_hooks._render_hook_block(str(REPO_ROOT),
                                                                               recorded_python=recorded)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n" + block)
    hook.chmod(0o755)


def _change(repo: Path, env: dict[str, str], body: str = "def a(x):\n    pass\n") -> subprocess.CompletedProcess:
    (repo / "mod.py").write_text(body)
    _run(repo, env, "add", "mod.py")
    return _run(repo, env, "commit", "-q", "-m", "change")


def _status(repo: Path) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, "-m", "agentteams.api_impact", "--repo", str(repo), "status",
                           "--range", "HEAD~1..HEAD", "--json"], cwd=REPO_ROOT, capture_output=True, text=True,
                          env={**_BASE_ENV, "PYTHONPATH": str(REPO_ROOT)}, check=False)
    return proc.returncode, json.loads(proc.stdout)


def test_only_an_old_python3_on_path_skips_visibly_and_status_fails(tmp_path: Path):
    env = _env([_fake_bin(tmp_path)])
    repo = _repo(tmp_path, env)
    _install(repo, recorded="")
    proc = _change(repo, env)
    assert proc.returncode == 0, proc.stderr  # the commit itself still goes through
    assert _SKIPPED in proc.stderr
    skips = _hook_python.read_skips(repo)
    assert len(skips) == 1 and "no Python 3.11+" in skips[0]
    rc, out = _status(repo)
    assert rc == 1 and out["hook_skips"] == skips


def test_the_recorded_interpreter_runs_the_check_and_clears_the_marker(tmp_path: Path):
    env = _env([_fake_bin(tmp_path)])
    repo = _repo(tmp_path, env)
    _install(repo, recorded="")
    _change(repo, env)
    assert _hook_python.read_skips(repo)
    _install(repo, recorded=sys.executable)
    proc = _change(repo, env, "def a(x, y):\n    pass\n")
    assert proc.returncode == 0, proc.stderr
    assert _SKIPPED not in proc.stderr and "API impact" in proc.stderr
    assert _hook_python.read_skips(repo) == []


def test_an_interpreter_inside_the_working_tree_is_never_used(tmp_path: Path):
    env = _env([_fake_bin(tmp_path)])
    repo = _repo(tmp_path, env)
    planted = repo / "tools" / "python3.12"
    planted.parent.mkdir()
    planted.write_text(f"#!/bin/sh\ntouch {repo}/PLANTED_RAN\nexec {sys.executable} \"$@\"\n")
    planted.chmod(0o755)
    _install(repo, recorded=str(planted))
    proc = _change(repo, {**env, "PATH": f"{planted.parent}{os.pathsep}{env['PATH']}"})
    assert _SKIPPED in proc.stderr
    assert not (repo / "PLANTED_RAN").exists()


def test_recorded_interpreter_is_refused_inside_the_repository(tmp_path: Path, monkeypatch):
    assert _hook_python.recorded_interpreter(tmp_path) == sys.executable or \
        Path(sys.executable).resolve().is_relative_to(tmp_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv" / "bin" / "python"))
    assert _hook_python.recorded_interpreter(tmp_path) == ""


def _lock(repo: Path) -> None:
    (repo / "toolchain.lock").write_text("agentteams=jlcatonjr/agentteams@" + "0" * 40 + "\nagentteams.tag=v1.1.0\n")


def test_a_release_locked_repository_never_falls_back_to_the_checkout(tmp_path: Path):
    env = _env([Path(sys.executable).parent])  # a good interpreter is on PATH
    repo = _repo(tmp_path, env)
    _lock(repo)
    _install(repo, recorded=sys.executable)
    proc = _change(repo, env)
    assert _SKIPPED in proc.stderr and "toolchain.lock pins agentteams" in proc.stderr
    assert "toolchain.lock" in _hook_python.read_skips(repo)[0]


def _locked_venv(repo: Path) -> None:
    """``<repo>/.venv`` whose site-packages puts this checkout on sys.path (a .pth survives ``-I``)."""
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(repo / ".venv")], check=True)
    site = next((repo / ".venv" / "lib").glob("python3*/site-packages"))
    (site / "agentteams-checkout.pth").write_text(str(REPO_ROOT) + "\n")


def test_a_release_locked_repository_uses_its_locked_installation(tmp_path: Path):
    env = _env([_fake_bin(tmp_path)])
    repo = _repo(tmp_path, env)
    _lock(repo)
    (repo / ".gitignore").write_text(".venv/\n")
    _locked_venv(repo)
    _install(repo, recorded="")
    proc = _change(repo, env)
    assert proc.returncode == 0, proc.stderr
    assert _SKIPPED not in proc.stderr and "API impact" in proc.stderr


def test_a_tracked_venv_is_not_trusted_as_the_locked_installation(tmp_path: Path):
    env = _env([_fake_bin(tmp_path)])
    repo = _repo(tmp_path, env)
    _lock(repo)
    _locked_venv(repo)
    _run(repo, env, "add", "-f", ".venv/pyvenv.cfg")
    _install(repo, recorded="")
    proc = _change(repo, env)
    assert _SKIPPED in proc.stderr


def test_status_without_a_marker_is_unchanged(tmp_path: Path):
    env = _env([Path(sys.executable).parent])
    repo = _repo(tmp_path, env)
    assert _hook_python.read_skips(repo) == []
    assert api_impact.main(["--repo", str(repo), "status", "--range", "HEAD..HEAD"]) == 0


@pytest.mark.parametrize("lock", ["agentteams.tag=v1.1.0\n", "tool=x\nnot a key value line\n", None])
def test_a_tag_only_unparsable_or_non_file_lock_counts_as_locked(tmp_path: Path, lock: str | None):
    """The same decision as agentteams._fleet_locks: in doubt, the repository is locked (no checkout fallback)."""
    env = _env([Path(sys.executable).parent])
    repo = _repo(tmp_path, env)
    if lock is None:
        (repo / "toolchain.lock").mkdir()
    else:
        (repo / "toolchain.lock").write_text(lock)
    _install(repo, recorded=sys.executable)
    assert "toolchain.lock pins agentteams" in _change(repo, env).stderr


def test_a_lock_without_an_agentteams_key_is_not_locked(tmp_path: Path):
    env = _env([_fake_bin(tmp_path)])
    repo = _repo(tmp_path, env)
    (repo / "toolchain.lock").write_text("# pins\nresearchteam=jlcatonjr/researchteam@abc\n")
    _install(repo, recorded=sys.executable)
    proc = _change(repo, env)
    assert _SKIPPED not in proc.stderr and "API impact" in proc.stderr


def test_a_symlink_outside_the_tree_pointing_into_it_is_refused(tmp_path: Path):
    env = _env([_fake_bin(tmp_path)])
    repo = _repo(tmp_path, env)
    planted = repo / "python3.12"
    planted.write_text(f"#!/bin/sh\ntouch {repo}/PLANTED_RAN\nexec {sys.executable} \"$@\"\n")
    planted.chmod(0o755)
    link = tmp_path / "outside" / "python3.12"
    link.parent.mkdir()
    link.symlink_to(planted)
    _install(repo, recorded=str(link))
    proc = _change(repo, env)
    assert _SKIPPED in proc.stderr and not (repo / "PLANTED_RAN").exists()


def test_a_crashing_check_is_recorded_and_does_not_clear_the_marker(tmp_path: Path):
    env = _env([_fake_bin(tmp_path)])
    repo = _repo(tmp_path, env)
    _install(repo, recorded="")
    _change(repo, env)
    fake = tmp_path / "fake-at" / "agentteams"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text("")
    (fake / "api_impact.py").write_text("raise RuntimeError('simulated crash')\n")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n" + git_hooks._render_hook_block(str(fake.parent), recorded_python=sys.executable))
    proc = _change(repo, env, "def a(x, y):\n    pass\n")
    assert proc.returncode == 0, proc.stderr
    skips = _hook_python.read_skips(repo)
    assert len(skips) == 2 and "failed (exit 1)" in skips[1]


def test_an_import_path_inside_the_repository_is_not_put_on_pythonpath(tmp_path: Path):
    inner = tmp_path / ".venv" / "lib" / "site-packages"
    inner.mkdir(parents=True)
    assert _hook_python.checkout_path(str(inner), tmp_path) == ""
    assert _hook_python.checkout_path(str(tmp_path), tmp_path) == str(tmp_path)  # the agentteams repo itself
    assert _hook_python.checkout_path("/opt/agentteams", tmp_path) == "/opt/agentteams"


def test_the_fixture_path_exposes_no_real_python(tmp_path: Path):
    """The skip tests mean something only when no real interpreter is reachable (GitHub runners have several)."""
    fake = _fake_bin(tmp_path)
    path = _env([fake])["PATH"]
    for name in _hook_python.PATH_CANDIDATES:
        found = shutil.which(name, path=path)
        assert found is None or Path(found).parent == fake, found
