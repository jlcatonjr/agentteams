"""#10 install-location check (criterion a) against simulated install layouts.

Apple-silicon Homebrew keeps its own git repository at its prefix and git-ignores ``Cellar/``, so a
pipx venv whose ``bin/python`` resolves into ``<brew>/Cellar/...`` must not be refused for the base
interpreter's path, while an editable checkout and a venv inside a checkout still are.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import agentteams
from agentteams.cli import signer_location

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git_init(root: Path, gitignore: str = "") -> None:
    """Make ``root`` a git work tree with an optional ``.gitignore``."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    if gitignore:
        (root / ".gitignore").write_text(gitignore, encoding="utf-8")


def _file(path: Path, text: str = "") -> Path:
    """Create ``path`` (and its parents) with ``text``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _brew(tmp_path: Path) -> Path:
    """A Homebrew-like prefix: a git repo that ignores everything but ``Library/``, with a Cellar python."""
    brew = tmp_path / "homebrew"
    _git_init(brew, "/*\n!/Library\n")
    _file(brew / "Library" / "Homebrew" / "brew.sh", "#!/bin/sh\n")
    return _file(brew / "Cellar" / "python@3.12" / "3.12.9" / "Frameworks" / "Python.framework"
                 / "Versions" / "3.12" / "bin" / "python3.12", "")


def _venv(root: Path, base_python: Path) -> tuple[Path, Path, Path]:
    """A venv at ``root`` whose ``bin/python`` symlinks to ``base_python``; returns (prefix, exe, pkg)."""
    exe = root / "bin" / "python"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.symlink_to(base_python)
    pkg_init = _file(root / "lib" / "python3.12" / "site-packages" / "agentteams" / "__init__.py")
    return root, exe, pkg_init


def _layout(monkeypatch, prefix: Path, exe: Path, base_prefix: Path, pkg_init: Path):
    """Point the running interpreter and package at a simulated layout; return a fresh module."""
    sl = importlib.reload(signer_location)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "base_prefix", str(base_prefix))
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(agentteams, "__file__", str(pkg_init))
    return sl


def _refusals(sl, cwd: Path) -> list[str]:
    return [f.message for f in sl.verify_install_location([], cwd=cwd) if f.refuse]


def test_pipx_venv_on_homebrew_python_is_allowed(tmp_path, monkeypatch):
    """pipx venv outside any repo, base interpreter in a git-ignored Cellar of the brew repo → allowed."""
    base = _brew(tmp_path)
    prefix, exe, pkg_init = _venv(tmp_path / "pipx" / "venvs" / "agentteams", base)
    sl = _layout(monkeypatch, prefix, exe, base.parents[1], pkg_init)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _refusals(sl, elsewhere) == []


def test_editable_checkout_is_still_refused(tmp_path, monkeypatch):
    """The package inside a git checkout (an editable install) is still refused, Homebrew or not."""
    base = _brew(tmp_path)
    checkout = tmp_path / "agentteams-checkout"
    _git_init(checkout, ".venv/\n")
    pkg_init = _file(checkout / "agentteams" / "__init__.py")
    prefix, exe, _ = _venv(checkout / ".venv", base)
    sl = _layout(monkeypatch, prefix, exe, base.parents[1], pkg_init)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    refusals = _refusals(sl, elsewhere)
    assert any(m.startswith("the agentteams package") and "git work tree" in m for m in refusals)
    # The venv is git-ignored inside the checkout but is still judged strictly.
    assert any(m.startswith("sys.prefix") and "git work tree" in m for m in refusals)
    assert not any(m.startswith("sys.executable") for m in refusals)


def test_venv_inside_any_repo_is_still_refused_even_if_ignored(tmp_path, monkeypatch):
    """A non-editable install into an ignored venv of some project repo: the package and prefix refuse."""
    base = _brew(tmp_path)
    project = tmp_path / "project"
    _git_init(project, ".venv/\n")
    prefix, exe, pkg_init = _venv(project / ".venv", base)
    sl = _layout(monkeypatch, prefix, exe, base.parents[1], pkg_init)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    refusals = _refusals(sl, elsewhere)
    assert any(m.startswith("the agentteams package") for m in refusals)
    assert any(m.startswith("sys.prefix") for m in refusals)


def test_base_interpreter_tracked_in_a_repo_is_still_refused(tmp_path, monkeypatch):
    """A base interpreter that is repository content (not git-ignored) is still refused."""
    repo = tmp_path / "pybuild"
    _git_init(repo)
    base = _file(repo / "bin" / "python3.12")
    prefix, exe, pkg_init = _venv(tmp_path / "pipx" / "venvs" / "agentteams", base)
    sl = _layout(monkeypatch, prefix, exe, repo, pkg_init)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    refusals = _refusals(sl, elsewhere)
    assert any(m.startswith("sys.executable") and "git work tree" in m for m in refusals)


def test_ignored_base_interpreter_still_refused_in_cwd_or_allow_write(tmp_path, monkeypatch):
    """The git-ignore allowance is only for the work-tree test: cwd and allowWrite still refuse."""
    import json

    base = _brew(tmp_path)
    prefix, exe, pkg_init = _venv(tmp_path / "pipx" / "venvs" / "agentteams", base)
    sl = _layout(monkeypatch, prefix, exe, base.parents[1], pkg_init)
    brew = tmp_path / "homebrew"
    assert any(m.startswith("sys.executable") and "current directory" in m
               for m in _refusals(sl, brew))
    proj = tmp_path / "proj"
    _file(proj / ".claude" / "settings.json",
          json.dumps({"sandbox": {"filesystem": {"allowWrite": [str(brew)]}}}))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    found = [f.message for f in sl.verify_install_location([proj], cwd=elsewhere) if f.refuse]
    assert any(m.startswith("sys.executable") and "agent-writable root" in m for m in found)


@pytest.mark.parametrize("env", [
    {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.excludesFile", "GIT_CONFIG_VALUE_0": "{ignore}"},
    {"GIT_CONFIG_GLOBAL": "{gitconfig}"},
])
def test_inherited_git_settings_cannot_make_a_tracked_interpreter_ignored(tmp_path, monkeypatch, env):
    """Only the work tree's own ignore rules count: env-injected or global excludes are not consulted."""
    repo = tmp_path / "repo"
    _git_init(repo)                                     # no .gitignore: everything is visible to git
    base = _file(repo / "py" / "bin" / "python3.12", "")
    ignore = _file(tmp_path / "evil-excludes", "*\n")
    gitconfig = _file(tmp_path / "evil.gitconfig", f"[core]\n\texcludesFile = {ignore}\n")
    for k, v in env.items():
        monkeypatch.setenv(k, v.format(ignore=ignore, gitconfig=gitconfig))
    prefix, exe, pkg_init = _venv(tmp_path / "pipx" / "venvs" / "agentteams", base)
    sl = _layout(monkeypatch, prefix, exe, base.parents[1], pkg_init)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert any(m.startswith("sys.executable") and "git work tree" in m for m in _refusals(sl, elsewhere))


@pytest.mark.parametrize("content", ["{not json", "[]", '{"sandbox": []}', '{"sandbox": {"filesystem": {"allowWrite": "x"}}}'])
def test_an_unreadable_settings_file_refuses_instead_of_meaning_no_roots(tmp_path, monkeypatch, content):
    """Unknown agent-writable roots refuse; a missing settings file or no sandbox block still means none."""
    base = _brew(tmp_path)
    prefix, exe, pkg_init = _venv(tmp_path / "pipx" / "venvs" / "agentteams", base)
    sl = _layout(monkeypatch, prefix, exe, base.parents[1], pkg_init)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    assert not [f for f in sl.verify_install_location([proj], cwd=elsewhere) if f.refuse]   # no settings file
    _file(proj / ".claude" / "settings.json", '{"permissions": {}}')
    assert not [f for f in sl.verify_install_location([proj], cwd=elsewhere) if f.refuse]   # no sandbox block
    _file(proj / ".claude" / "settings.json", content)
    found = [f.message for f in sl.verify_install_location([proj], cwd=elsewhere) if f.refuse]
    assert any("cannot be read or parsed" in m for m in found), found

