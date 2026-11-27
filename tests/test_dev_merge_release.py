"""1.3.2: ``--merge-pr`` refuses release mechanics, pin moves and enforcement-code changes (D1, D3, D5).

Each test builds a real origin repository (``main`` with a ``v1.3.0`` tag, ``release/1.3`` and ``dev``) and a pull
request branch, then runs :func:`dev_merge_release.release_problem` against it as a fetch URL.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentteams.cli import dev_merge_release as dmr

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}


def _g(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *argv], cwd=repo, env=_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


def _local_git(argv, cwd):
    # The verb's runner without the gh credential helper (the origin is a local path).
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *argv], cwd=cwd, env=_ENV,
                          capture_output=True, text=True, errors="replace", check=False)


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _commit(repo: Path, msg: str, **files: str) -> str:
    for rel, text in files.items():
        _write(repo, rel.replace("__", "/"), text)
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", msg)
    return _g(repo, "rev-parse", "HEAD")


_PYPROJECT = '[project]\nname = "x"\nversion = "{v}"\n\n[build-system]\nrequires = ["setuptools==84.2.1"]\n'
_MANIFEST = json.dumps({"version": 1, "note": "n", "modules": {"agentteams/cli/dev_merge.py": "0" * 64}})


@pytest.fixture()
def origin(tmp_path: Path) -> Path:
    """An origin with main (tagged v1.3.0), release/1.3 (one release-only commit) and dev (1.4.0.dev0)."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _g(repo, "init", "-q", "-b", "main")
    _commit(repo, "base", **{"pyproject.toml": _PYPROJECT.format(v="1.3.0"), "CHANGELOG.md": "# C\n\n## [1.3.0] - d\n",
                             "references__enforcement-integrity.json": _MANIFEST, "src__app.py": "x = 1\n",
                             "agentteams__cli__dev_merge.py": "GATE = 1\n",
                             ".github__workflows__ci.yml": "run: agentteams --verify-toolchain-pin tools/pins.lock\n"})
    _g(repo, "tag", "v1.3.0")
    _g(repo, "switch", "-q", "-c", "release/1.3")
    _commit(repo, "release-only fix", **{"src__fix.py": "fixed = True\n"})
    _g(repo, "switch", "-q", "-c", "dev", "v1.3.0")
    _commit(repo, "dev is 1.4.0.dev0", **{"pyproject.toml": _PYPROJECT.format(v="1.4.0.dev0")})
    return repo


def _pr(origin: Path, name: str, **files: str) -> str:
    _g(origin, "switch", "-q", "-c", name, "dev")
    sha = _commit(origin, name, **files) if files else _g(origin, "rev-parse", "HEAD")
    _g(origin, "switch", "-q", "dev")
    return sha


def _check(origin: Path, name: str, sha: str, files: list[str]) -> str | None:
    return dmr.release_problem(repo_url=str(origin), base_branch="dev", head_ref=f"refs/heads/{name}", head_sha=sha,
                               files=files, git=_local_git)


def test_a_feature_pull_request_still_qualifies(origin):
    sha = _pr(origin, "feat", **{"src__app.py": "x = 2\n"})
    assert _check(origin, "feat", sha, ["src/app.py"]) is None


def test_a_forward_merge_is_refused_by_ancestry(origin):
    _g(origin, "switch", "-q", "-c", "fwd", "dev")
    _g(origin, "merge", "-q", "--no-edit", "-X", "ours", "release/1.3")
    sha = _g(origin, "rev-parse", "HEAD")
    _g(origin, "switch", "-q", "dev")
    assert "forward merge" in (_check(origin, "fwd", sha, ["src/fix.py"]) or "")


def test_a_cherry_picked_release_commit_is_refused_by_patch_id(origin):
    _g(origin, "switch", "-q", "-c", "pick", "dev")
    _g(origin, "cherry-pick", "release/1.3")
    sha = _g(origin, "rev-parse", "HEAD")
    _g(origin, "switch", "-q", "dev")
    assert "same patch as a release-only commit" in (_check(origin, "pick", sha, ["src/fix.py"]) or "")


@pytest.mark.parametrize("files,why", [
    ({"pyproject.toml": _PYPROJECT.format(v="1.4.0")}, "project.version"),
    ({"pyproject.toml": '[project]\nname = "x"\ndynamic = ["version"]\n\n[build-system]\nrequires = ["setuptools==84.2.1"]\n'},
     "project.dynamic"),
    ({"pyproject.toml": _PYPROJECT.format(v="1.4.0.dev0") + '\n[project.scripts]\nagentteams = "elsewhere:main"\n'},
     "project.scripts"),
    ({"pyproject.toml": _PYPROJECT.format(v="1.4.0.dev0") + '\n[tool.setuptools]\npy-modules = ["shadow"]\n'},
     "tool.setuptools"),
    ({"pyproject.toml": _PYPROJECT.format(v="1.4.0.dev0").replace('[build-system]',
                                                                   'dependencies = ["requests"]\n\n[build-system]')},
     "project.dependencies"),
    ({"CHANGELOG.md": "# C\n\n## [1.4.0] - d\n\n## [1.3.0] - d\n"}, "CHANGELOG release section"),
    ({"agentteams__capability_hints.py": 'LAST_RELEASE_TAG = "v1.4.0"\n'}, "LAST_RELEASE_TAG"),
])
def test_release_commits_are_refused(origin, files, why):
    sha = _pr(origin, "rel", **files)
    assert why in (_check(origin, "rel", sha, [k.replace("__", "/") for k in files]) or "")


@pytest.mark.parametrize("rel,text", [
    ("toolchain.lock", "agentteams=jlcatonjr/agentteams@" + "a" * 40 + "\n"),
    ("tools/pins.lock", "anything\n"),                    # named by the base's workflow
    ("deps/versions.txt", "lib=owner/lib@" + "b" * 40 + "\n"),   # a pin line in an unlisted file
    ("agentteams/cli/dev_merge.py", "GATE = 0\n"),        # a module the base's manifest pins
    ("setup.py", "import setuptools; setuptools.setup()\n"),   # packaging inputs setuptools reads at install
    ("setup.cfg", "[metadata]\nname = x\n"),
    ("MANIFEST.in", "include *.txt\n"),
    ("agentteams/cli/DEV_MERGE.py", "GATE = 0\n"),       # case-insensitive collision with a pinned module
    ("Setup.py", "import setuptools\n"),
    ("src/evil.pth", "import os\n"),                     # start-up hooks run at every interpreter start
    ("lib/sitecustomize.py", "print(1)\n"),
    ("shadow.py", "x = 1\n"),                            # a new top-level module could shadow an import
    ("shadow.so", "binary\n"),                           # so could a compiled one
    ("agentteams/cli/dev_merge.cpython-312.so", "x\n"),  # a compiled module beside a pinned one shadows it
    ("agentteams/cli/dev_merge/__init__.py", "x = 1\n"),  # so does a package (Python prefers it at import)
    ("agentteams/cli/__pycache__/dev_merge.cpython-312.pyc", "x\n"),
    (".github/docs-requirements.txt", "mkdocs==1.6.1\n"),  # a requirements file anywhere
    ("requirements/dev.txt", "pytest==9.0.3\n"),
    ("pyproject.toml", _PYPROJECT.format(v="1.4.0.dev0") + '\n[tool.pytest.ini_options]\naddopts = "-k not gate"\n'),
])
def test_pins_and_enforcement_code_are_refused(origin, rel, text):
    sha = _pr(origin, "pin", **{rel.replace("/", "__"): text})
    assert "the operator merges" in (_check(origin, "pin", sha, [rel]) or "")


def test_a_new_top_level_package_is_refused(origin):
    # A new package at the repository root could shadow an import (e.g. a top-level `json`).
    _g(origin, "switch", "-q", "-c", "pkg", "dev")
    _write(origin, "json/__init__.py", "x = 1\n")
    _g(origin, "add", "-A")
    _g(origin, "commit", "-q", "-m", "shadow json")
    sha = _g(origin, "rev-parse", "HEAD")
    _g(origin, "switch", "-q", "dev")
    assert "the operator merges" in (_check(origin, "pkg", sha, ["json/__init__.py"]) or "")


def test_removing_or_deleting_a_pin_is_refused(origin):
    # The base holds a pin line in an unlisted file; a PR that removes the line, or deletes the file, moves the pin.
    _g(origin, "switch", "-q", "dev")
    _commit(origin, "add a pin file", **{"deps__pins.txt": "lib=owner/lib@" + "e" * 40 + "\n"})
    edit = _pr(origin, "unpin", **{"deps__pins.txt": "nothing pinned here\n"})
    assert "the operator merges" in (_check(origin, "unpin", edit, ["deps/pins.txt"]) or "")
    _g(origin, "switch", "-q", "-c", "drop", "dev")
    _g(origin, "rm", "-q", "deps/pins.txt")
    _g(origin, "commit", "-q", "-m", "drop the pin file")
    drop = _g(origin, "rev-parse", "HEAD")
    _g(origin, "switch", "-q", "dev")
    assert "the operator merges" in (_check(origin, "drop", drop, ["deps/pins.txt"]) or "")


def test_any_change_to_capability_hints_is_refused(origin):
    # The merge gate imports it and it is not pinned (it changes every release), so every change is refused.
    sha = _pr(origin, "hints", **{"agentteams__capability_hints.py": "HINT = 'reworded'\n"})
    assert "the operator merges" in (_check(origin, "hints", sha, ["agentteams/capability_hints.py"]) or "")


@pytest.mark.parametrize("rel", [
    "agentteams/capability_hints/__init__.py",                       # shadows the unpinned exemption (IM F1)
    "agentteams/capability_hints.abi3.so",
    "agentteams/__pycache__/capability_hints.cpython-312.pyc",
    "agentteams/cli/__init__.abi3.so",                               # shadows a pinned __init__ (IM F2)
    "agentteams/cli/__pycache__/__init__.cpython-312.pyc",
    "src/anything.pyc",                                              # compiled code anywhere
    "docs/__pycache__/notes.txt",                                    # anything under __pycache__/
])
def test_compiled_code_and_shadows_of_any_route_module_are_refused(origin, rel):
    _g(origin, "switch", "-q", "-c", "shadow", "dev")
    _write(origin, rel, "x = 1\n")
    _g(origin, "add", "-f", "-A")
    _g(origin, "commit", "-q", "-m", "shadow")
    sha = _g(origin, "rev-parse", "HEAD")
    _g(origin, "switch", "-q", "dev")
    assert "the operator merges" in (_check(origin, "shadow", sha, [rel]) or "")


def test_a_submodule_or_gitmodules_change_is_refused(origin):
    _g(origin, "switch", "-q", "-c", "sub", "dev")
    _g(origin, "update-index", "--add", "--cacheinfo", f"160000,{'f' * 40},vendor/lib")
    _write(origin, ".gitmodules", '[submodule "vendor/lib"]\n\tpath = vendor/lib\n\turl = https://x/lib\n')
    _g(origin, "add", ".gitmodules")
    _g(origin, "commit", "-q", "-m", "a submodule")
    sha = _g(origin, "rev-parse", "HEAD")
    _g(origin, "switch", "-q", "dev")
    assert "the operator merges" in (_check(origin, "sub", sha, ["vendor/lib"]) or "")
    assert "the operator merges" in (_check(origin, "sub", sha, [".gitmodules"]) or "")


def test_a_v_prefixed_release_heading_is_refused(origin):
    sha = _pr(origin, "vhead", **{"CHANGELOG.md": "# C\n\n## [v1.4.0] - d\n\n## [1.3.0] - d\n"})
    assert "CHANGELOG release section" in (_check(origin, "vhead", sha, ["CHANGELOG.md"]) or "")


def test_a_binary_change_is_read_without_failing(origin):
    _g(origin, "switch", "-q", "-c", "bin", "dev")
    (origin / "img.bin").write_bytes(bytes(range(256)) * 4)
    _g(origin, "add", "-A")
    _g(origin, "commit", "-q", "-m", "binary")
    sha = _g(origin, "rev-parse", "HEAD")
    _g(origin, "switch", "-q", "dev")
    assert _check(origin, "bin", sha, ["img.bin"]) is None


def test_workflow_pip_files_are_named():
    assert dmr.workflow_lockfiles(["run: pip install -r .github/docs-requirements.txt",
                                   "pip install --constraint=c/pins.txt -e .", "x: tools/a.lock"]) == {
        ".github/docs-requirements.txt", "c/pins.txt", "tools/a.lock"}


def test_a_moved_head_is_refused(origin):
    _pr(origin, "feat2", **{"src__app.py": "x = 3\n"})
    assert "head moved" in (_check(origin, "feat2", "c" * 40, ["src/app.py"]) or "")


def test_an_unreachable_origin_fails_closed(origin, tmp_path):
    with pytest.raises(dmr.ReleaseCheckError):
        dmr.release_problem(repo_url=str(tmp_path / "nowhere"), base_branch="dev", head_ref="refs/heads/x",
                            head_sha="d" * 40, files=[], git=_local_git)


def test_an_unparseable_pyproject_or_manifest_fails_closed():
    with pytest.raises(dmr.ReleaseCheckError):
        dmr.version_problem("[project\n", "", None, None, None, None)
    with pytest.raises(dmr.ReleaseCheckError):
        dmr.pinned_modules(json.dumps({"modules": {}}))
    assert dmr.pinned_modules(None) == set()   # a repository with no manifest pins nothing
