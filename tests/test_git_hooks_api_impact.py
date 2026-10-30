"""The pre-commit hook's API-impact clause: rendered in the refresh block, reports; it fails a commit only in
opt-in strict mode (git config agentteams.apiDocsStrict, exit 3), never on a crash."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
import sys
from pathlib import Path

import pytest

from agentteams import api_impact, git_hooks

REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1"}


def test_clause_is_inside_the_refresh_block_before_its_exit():
    block = git_hooks._render_hook_block("/opt/at")
    clause = block.index("agentteams.api_impact --pre-commit")
    assert block.index(git_hooks._HOOK_BEGIN) < clause < block.index("exit $_at_rc")
    assert "python3 -P -m agentteams.api_impact" in block
    assert "git diff --cached --name-only 2>/dev/null)" in block  # unfiltered: deletions count
    line = block[block.rindex("\n", 0, clause) + 1:block.index("\n", clause)]
    assert "--strict-capable" in line
    assert line.rstrip().endswith("|| _at_api=$?")
    assert "|| true" not in line


def test_clause_sets_rc_only_on_exit_3():
    block = git_hooks._render_hook_block("/opt/at")
    lines = [ln.strip() for ln in block.splitlines()]
    i = next(n for n, ln in enumerate(lines) if "agentteams.api_impact --pre-commit" in ln)
    assert lines[i - 1] == "_at_api=0"
    assert lines[i + 1] == 'if [ "$_at_api" -eq 3 ]; then _at_rc=1; fi'
    assert sum("_at_rc=1" in ln for ln in lines) == 1
    assert "agentteams.apiDocsStrict" in block  # the comment names the opt-in


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], env=_ENV, capture_output=True, text=True, check=False)


@pytest.mark.skipif(shutil.which("git") is None or sys.version_info < (3, 11), reason="git and Python 3.11+")
@pytest.mark.parametrize("at_path,expect", [(str(REPO_ROOT), "API impact"), ("/nonexistent", "api-impact")])
def test_commit_reports_impact_and_succeeds_even_when_the_check_fails(tmp_path: Path, at_path: str, expect: str):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "mod.py").write_text("def a():\n    pass\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "--no-verify", "-m", "base")
    git_hooks.install_pre_commit_hook(tmp_path, agentteams_path=at_path)
    (tmp_path / "mod.py").write_text("def a(x):\n    pass\n")
    _git(tmp_path, "add", "-A")
    proc = _git(tmp_path, "commit", "-q", "-m", "change a")
    assert proc.returncode == 0, proc.stderr
    if at_path == str(REPO_ROOT):
        assert expect in proc.stderr
    assert _git(tmp_path, "log", "--oneline").stdout.count("\n") == 2


# ---------------------------------------------------------------------------
# Strict mode (git config agentteams.apiDocsStrict), end to end
# ---------------------------------------------------------------------------

_needs_git = pytest.mark.skipif(shutil.which("git") is None or shutil.which("python3") is None
                                or sys.version_info < (3, 11), reason="git and Python 3.11+")
_HOOK_ENV = {k: v for k, v in _ENV.items() if k != "PYTHONPATH"}  # only the baked path decides the import
_BLOCKED = "commit blocked (agentteams.apiDocsStrict)"
_PREDATES = "this pre-commit hook predates strict mode"


def _hg(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], env=_HOOK_ENV, capture_output=True, text=True,
                          check=False)


def _repo(tmp_path: Path, body: str | None = None) -> Path:
    """A repo with module pkg/mod.py and its page docs/mod.md; optional pre-existing hook body."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _hg(repo, "init", "-q", "-b", "main")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "mod.py").write_text("def a():\n    pass\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "mod.md").write_text("# mod\n\n`a()` does a.\n")
    _hg(repo, "add", "-A")
    assert _hg(repo, "commit", "-q", "--no-verify", "-m", "base").returncode == 0
    if body is not None:
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(body)
        hook.chmod(0o755)
    return repo


def _crashing_agentteams(tmp_path: Path) -> str:
    """A baked import path whose agentteams.api_impact raises on import (a reliable crash, exit 1)."""
    pkg = tmp_path / "fake-at" / "agentteams"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "api_impact.py").write_text("raise RuntimeError('simulated api_impact crash')\n")
    return str(pkg.parent)


def _add_public_function(repo: Path) -> None:
    (repo / "pkg" / "mod.py").write_text("def a():\n    pass\n\n\ndef brand_new():\n    pass\n")
    _hg(repo, "add", "pkg/mod.py")


def _strict_on(repo: Path) -> None:
    assert _hg(repo, "config", "agentteams.apiDocsStrict", "true").returncode == 0


def _commit(repo: Path) -> subprocess.CompletedProcess:
    return _hg(repo, "commit", "-q", "-m", "change")


def _commits(repo: Path) -> str:
    return _hg(repo, "rev-list", "--count", "HEAD").stdout.strip()


@_needs_git
def test_strict_off_reports_unupdated_page_and_succeeds(tmp_path: Path):
    repo = _repo(tmp_path)
    git_hooks.install_pre_commit_hook(repo, agentteams_path=str(REPO_ROOT))
    _add_public_function(repo)
    proc = _commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert "agentteams: API docs — pkg/mod.py: docs/mod.md not updated (undocumented: brand_new" in proc.stderr
    assert _BLOCKED not in proc.stderr
    assert _commits(repo) == "2"


@_needs_git
def test_strict_on_blocks_unupdated_page(tmp_path: Path):
    repo = _repo(tmp_path)
    git_hooks.install_pre_commit_hook(repo, agentteams_path=str(REPO_ROOT))
    _strict_on(repo)
    _add_public_function(repo)
    proc = _commit(repo)
    assert proc.returncode != 0, proc.stderr
    assert _BLOCKED in proc.stderr
    assert "agentteams: API docs — pkg/mod.py: docs/mod.md not updated" in proc.stderr
    assert _commits(repo) == "1"


@_needs_git
def test_strict_on_succeeds_when_page_is_staged(tmp_path: Path):
    repo = _repo(tmp_path)
    git_hooks.install_pre_commit_hook(repo, agentteams_path=str(REPO_ROOT))
    _strict_on(repo)
    _add_public_function(repo)
    (repo / "docs" / "mod.md").write_text("# mod\n\n`a()` does a. `brand_new()` is new.\n")
    _hg(repo, "add", "docs/mod.md")
    proc = _commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert _BLOCKED not in proc.stderr
    assert _commits(repo) == "2"


@_needs_git
def test_strict_on_blocks_new_module_without_page(tmp_path: Path):
    repo = _repo(tmp_path)
    git_hooks.install_pre_commit_hook(repo, agentteams_path=str(REPO_ROOT))
    _strict_on(repo)
    (repo / "pkg" / "fresh.py").write_text("def fresh_fn():\n    pass\n")
    _hg(repo, "add", "pkg/fresh.py")
    proc = _commit(repo)
    assert proc.returncode != 0, proc.stderr
    assert "agentteams: API docs — pkg/fresh.py: new module with no API page" in proc.stderr
    assert _BLOCKED in proc.stderr
    assert _commits(repo) == "1"


@_needs_git
@pytest.mark.parametrize("set_e", [False, True])
def test_strict_on_crash_never_blocks(tmp_path: Path, set_e: bool):
    repo = _repo(tmp_path, "#!/bin/sh\nset -e\n" if set_e else None)
    git_hooks.install_pre_commit_hook(repo, agentteams_path=_crashing_agentteams(tmp_path))
    _strict_on(repo)
    _add_public_function(repo)
    proc = _commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert "simulated api_impact crash" in proc.stderr  # the clause really ran and crashed
    assert _BLOCKED not in proc.stderr
    assert _commits(repo) == "2"


@_needs_git
def test_set_e_body_still_blocks_in_strict_mode(tmp_path: Path):
    repo = _repo(tmp_path, "#!/bin/sh\nset -e\n")
    git_hooks.install_pre_commit_hook(repo, agentteams_path=str(REPO_ROOT))
    assert (repo / ".git" / "hooks" / "pre-commit").read_text().startswith("#!/bin/sh\nset -e\n")
    _strict_on(repo)
    _add_public_function(repo)
    proc = _commit(repo)
    assert proc.returncode != 0, proc.stderr
    assert _BLOCKED in proc.stderr
    assert _commits(repo) == "1"


@_needs_git
def test_failing_hook_body_still_fails_with_strict_off(tmp_path: Path):
    repo = _repo(tmp_path, "#!/bin/sh\necho 'user body ran' >&2\nfalse\n")
    git_hooks.install_pre_commit_hook(repo, agentteams_path=str(REPO_ROOT))
    _add_public_function(repo)
    proc = _commit(repo)
    assert proc.returncode != 0, proc.stderr
    assert "user body ran" in proc.stderr
    assert "agentteams: API docs — pkg/mod.py" in proc.stderr  # the API clause ran and exited 0
    assert _commits(repo) == "1"


@_needs_git
def test_old_clause_without_strict_capable_never_blocks(tmp_path: Path):
    # An old hook's clause: no --strict-capable (and here no `|| true`, so the exit code alone decides).
    body = textwrap.dedent(f"""\
        #!/bin/sh
        set -e
        PYTHONPATH="{REPO_ROOT}" python3 -P -m agentteams.api_impact --pre-commit --repo "$(git rev-parse --show-toplevel)"
        """)
    repo = _repo(tmp_path, body)
    _strict_on(repo)
    _add_public_function(repo)
    proc = _commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert _PREDATES in proc.stderr
    assert _BLOCKED not in proc.stderr
    assert _commits(repo) == "2"


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------

@_needs_git
@pytest.mark.parametrize("strict,capable,page_staged,rc", [
    (True, True, False, 3),
    (True, True, True, 0),
    (True, False, False, 0),
    (False, True, False, 0),
    (False, False, False, 0),
])
def test_cli_pre_commit_exit_codes(tmp_path: Path, monkeypatch, capsys, strict, capable, page_staged, rc):
    for k, v in _HOOK_ENV.items():
        if k.startswith("GIT_"):
            monkeypatch.setenv(k, v)
    repo = _repo(tmp_path)
    if strict:
        _strict_on(repo)
    _add_public_function(repo)
    if page_staged:
        (repo / "docs" / "mod.md").write_text("# mod\n\n`a()`, `brand_new()`.\n")
        _hg(repo, "add", "docs/mod.md")
    argv = ["--pre-commit", "--repo", str(repo)] + (["--strict-capable"] if capable else [])
    assert api_impact.main(argv) == rc
    err = capsys.readouterr().err
    assert (_BLOCKED in err) == (rc == 3)
    assert (_PREDATES in err) == (strict and not capable)
