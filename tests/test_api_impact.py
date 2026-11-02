"""Tests for agentteams.api_impact — API-impact detection and the review ledger.

Every test builds a throwaway git repository, so the detection is checked against real git diffs (renames,
deletions, the index) rather than against hand-built inputs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import api_impact as ai

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")

_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], env=_ENV, check=True, capture_output=True,
                          text=True).stdout


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _write(tmp_path, "pkg/core.py", "def run(a, b=1):\n    return a\n\nclass Tool:\n    def go(self):\n        pass\n")
    _write(tmp_path, "references/README.md", "refs\n")
    _commit(tmp_path, "base")
    _git(tmp_path, "switch", "-q", "-c", "feat/x")
    return tmp_path


# --- surface ---------------------------------------------------------------

def test_surface_covers_functions_classes_methods_all_and_cli():
    src = (
        "import argparse\n__all__ = ['b', 'a']\n"
        "def a(x, *, y=2) -> int: ...\n"
        "def _private(): ...\n"
        "class C(Base):\n    def __init__(self, n): ...\n    def m(self): ...\n    def _h(self): ...\n"
        "p = argparse.ArgumentParser()\np.add_argument('--flag', '-f')\nsub = p.add_subparsers()\n"
        "sub.add_parser('serve')\n"
    )
    s = ai.surface(src)
    assert s["a"] == "(x, *, y=2) -> int"
    assert "_private" not in s and "C._h" not in s
    assert s["C"] == "(Base)" and "C.__init__" in s and "C.m" in s
    assert s["__all__"] == "['a', 'b']"
    assert {"cli:--flag", "cli:-f", "cli-cmd:serve"} <= s.keys()


def test_surface_of_unparseable_source_is_none():
    assert ai.surface("def (:\n") is None


# --- detect ----------------------------------------------------------------

def test_staged_signature_symbol_and_method_changes(repo: Path):
    _write(repo, "pkg/core.py", "def run(a, b=2):\n    return a\n\ndef new():\n    pass\n\nclass Tool:\n    pass\n")
    _git(repo, "add", "-A")
    imp = ai.detect(repo)
    assert imp.symbols_changed == ["pkg/core.py::run"]
    assert imp.symbols_added == ["pkg/core.py::new"]
    assert imp.symbols_removed == ["pkg/core.py::Tool.go"]


def test_rename_and_deletion_are_both_visible(repo: Path):
    _write(repo, "pkg/other.py", "def gone():\n    pass\n")
    _commit(repo, "add other")
    _git(repo, "mv", "pkg/core.py", "pkg/engine.py")
    _git(repo, "rm", "-q", "pkg/other.py")
    imp = ai.detect(repo)
    assert imp.modules_renamed == [("pkg/core.py", "pkg/engine.py")]
    assert imp.modules_removed == ["pkg/other.py"]
    assert "pkg/other.py::gone" in imp.symbols_removed


def test_cli_schema_and_structural_changes(repo: Path):
    _write(repo, "pkg/cli.py", "def build(p):\n    p.add_argument('--new-flag')\n")
    _write(repo, "schemas/brief.schema.json", "{}\n")
    _write(repo, "templates/new.template.md", "x\n")
    _git(repo, "add", "-A")
    imp = ai.detect(repo)
    assert imp.cli_added == ["--new-flag"]
    assert imp.schemas_changed == ["schemas/brief.schema.json"]
    assert "templates/new.template.md" in imp.structural_added


def test_tests_docs_work_records_and_snapshots_are_not_impact(repo: Path):
    for rel in ("tests/test_core.py", "docs_src/page.md", "tmp/by-week/x.plan.md", "workSummaries/d.md",
                "references/plans/p.plan.md", "examples/demo/expected/out.md"):
        _write(repo, rel, "def test_x():\n    pass\n" if rel.endswith(".py") else "x\n")
    _git(repo, "add", "-A")
    assert not ai.detect(repo).impactful


def test_detect_on_a_commit_and_on_the_root_commit(repo: Path):
    _write(repo, "pkg/core.py", "def run(a):\n    return a\n")
    sha = _commit(repo, "narrow run")
    assert ai.detect(repo, commit=sha).symbols_changed == ["pkg/core.py::run"]
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    assert "pkg/core.py" in ai.detect(repo, commit=root).modules_added


# --- base, status, record ---------------------------------------------------

def test_status_flags_an_unrecorded_commit_and_record_clears_it(repo: Path):
    _write(repo, "pkg/core.py", "def run(a):\n    return a\n")
    sha = _commit(repo, "narrow run")
    used, commits = ai.status(repo)
    assert used is not None and [c.commit for c in commits] == [sha]
    assert not commits[0].reviewed
    assert ai.main(["--repo", str(repo), "status"]) == 1
    written = ai.record(repo, "updated docs for run()")
    assert len(written) == 1 and written[0].parent == repo / "references" / ai.LEDGER_SUBDIR
    assert json.loads(written[0].read_text())["note"] == "updated docs for run()"
    assert not any(c.reviewed for c in ai.status(repo)[1])  # an uncommitted record does not count
    assert ai.record(repo, "again") == []  # nor is it written twice
    _commit(repo, "record review")
    assert all(c.reviewed for c in ai.status(repo)[1])
    assert ai.main(["--repo", str(repo), "status"]) == 0


def test_record_refuses_an_empty_note(repo: Path):
    _write(repo, "pkg/core.py", "def run(a):\n    return a\n")
    _commit(repo, "narrow run")
    with pytest.raises(ValueError, match="non-empty"):
        ai.record(repo, "  ")
    assert ai.main(["--repo", str(repo), "record", "--note", ""]) == 2


def test_committing_the_ledger_neither_counts_as_impact_nor_unreviews(repo: Path):
    _write(repo, "pkg/core.py", "def run(a):\n    return a\n")
    _commit(repo, "narrow run")
    ai.record(repo, "reviewed")
    _commit(repo, "record review")
    _, commits = ai.status(repo)
    assert len(commits) == 1 and commits[0].reviewed


def test_review_survives_a_rebase(repo: Path):
    _write(repo, "pkg/core.py", "def run(a):\n    return a\n")
    _commit(repo, "narrow run")
    ai.record(repo, "reviewed")
    _commit(repo, "record review")
    _git(repo, "switch", "-q", "main")
    _write(repo, "README.md", "unrelated\n")
    _commit(repo, "unrelated on main")
    _git(repo, "switch", "-q", "feat/x")
    _git(repo, "rebase", "-q", "main")
    _, commits = ai.status(repo)
    assert len(commits) == 1 and commits[0].reviewed


def test_on_the_default_branch_without_upstream_no_base_means_pending(repo: Path):
    _git(repo, "switch", "-q", "main")
    assert ai.resolve_base(repo) is None
    assert ai.main(["--repo", str(repo), "status"]) == 2


def test_checklist_names_files_that_mention_a_removed_symbol(repo: Path):
    _write(repo, "docs_src/api.md", "Call `run` to start.\n")
    _commit(repo, "docs")
    _write(repo, "pkg/core.py", "class Tool:\n    def go(self):\n        pass\n")
    _commit(repo, "remove run")
    _, commits = ai.status(repo)
    assert commits[-1].checklist["run"] == ["docs_src/api.md"]


# --- pre-commit ---------------------------------------------------------------

def test_pre_commit_reports_and_never_fails(repo: Path, capsys):
    _write(repo, "pkg/core.py", "def run():\n    pass\n")
    _git(repo, "add", "-A")
    assert ai.main(["--pre-commit", "--repo", str(repo)]) == 0
    assert "API impact" in capsys.readouterr().err
    assert ai.main(["--pre-commit", "--repo", str(repo / "not-a-repo")]) == 0


def _fake_agentteams_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "agentteams.regenerateMan", "true")
    _write(tmp_path, "build_team.py", "def _build_parser():\n    return None\n")
    _write(tmp_path, "agentteams/__init__.py", "")
    _write(tmp_path, "agentteams/man.py", "import build_team, sys\nsys.stdout.write('.TH NEW\\n')\n")
    _write(tmp_path, "agentteams.1", ".TH OLD\n")
    _commit(tmp_path, "base")
    return tmp_path


def test_man_page_is_regenerated_and_staged_when_the_parser_is_staged(tmp_path: Path):
    repo = _fake_agentteams_repo(tmp_path)
    _write(repo, "build_team.py", "def _build_parser():\n    return 1\n")
    _git(repo, "add", "build_team.py")
    msg = ai.pre_commit(repo)
    assert (repo / "agentteams.1").read_text() == ".TH NEW\n"
    assert "agentteams.1" in _git(repo, "diff", "--cached", "--name-only")
    assert "regenerated" in msg


def test_man_page_is_not_regenerated_over_unstaged_parser_edits(tmp_path: Path):
    repo = _fake_agentteams_repo(tmp_path)
    _write(repo, "build_team.py", "def _build_parser():\n    return 1\n")
    _git(repo, "add", "build_team.py")
    _write(repo, "build_team.py", "def _build_parser():\n    return 2\n")
    msg = ai.pre_commit(repo)
    assert (repo / "agentteams.1").read_text() == ".TH OLD\n"
    assert "unstaged edits" in msg


def test_man_page_regeneration_is_off_without_the_git_config_opt_in(tmp_path: Path):
    repo = _fake_agentteams_repo(tmp_path)
    _git(repo, "config", "--unset", "agentteams.regenerateMan")
    _write(repo, "build_team.py", "def _build_parser():\n    return 1\n")
    _git(repo, "add", "build_team.py")
    ai.pre_commit(repo)
    assert (repo / "agentteams.1").read_text() == ".TH OLD\n"


def test_a_pushed_feature_branch_without_a_default_branch_is_pending_not_passed(tmp_path: Path):
    _git(tmp_path, "init", "-q", "-b", "trunk")
    _write(tmp_path, "m.py", "def a():\n    pass\n")
    _commit(tmp_path, "base")
    assert ai.resolve_base(tmp_path) is None


def test_on_the_default_branch_the_unpushed_commits_are_the_range(repo: Path, tmp_path_factory):
    remote = tmp_path_factory.mktemp("remote")
    _git(remote, "init", "-q", "--bare")
    _git(repo, "switch", "-q", "main")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _write(repo, "pkg/core.py", "def run(a):\n    return a\n")
    sha = _commit(repo, "direct to main")
    used, commits = ai.status(repo)
    assert used is not None and [c.commit for c in commits] == [sha]


def test_a_root_file_moved_into_docs_still_counts_as_a_structural_change(repo: Path):
    _write(repo, "NOTES.txt", "n\n")
    _commit(repo, "notes")
    (repo / "docs").mkdir()
    _git(repo, "mv", "NOTES.txt", "docs/NOTES.txt")
    assert ("NOTES.txt", "docs/NOTES.txt") in ai.detect(repo).structural_renamed


# --- status: API docs (plan api-doc-map, I1-2) -------------------------------------

def _docs_repo(repo: Path) -> None:
    _git(repo, "switch", "-q", "main")
    _write(repo, "docs_src/core.md", "Call `run` to start.\n")
    _commit(repo, "core page")
    _git(repo, "switch", "-q", "feat/x")
    _git(repo, "rebase", "-q", "main")


def _status_output(repo: Path, capsys) -> tuple[int, str]:
    capsys.readouterr()
    rc = ai.main(["--repo", str(repo), "status"])
    return rc, capsys.readouterr().out


def test_status_reports_an_undocumented_added_symbol(repo: Path, capsys):
    _docs_repo(repo)
    _write(repo, "pkg/core.py", (repo / "pkg/core.py").read_text() + "\ndef new_api():\n    pass\n")
    _commit(repo, "add new_api")
    _, commits = ai.status(repo)
    [ds] = [d for d in commits[0].docs if d.module == "pkg/core.py"]
    assert ds.pages == ["docs_src/core.md"] and ds.state == "not-updated"
    assert ds.undocumented == ["new_api"]
    rc, out = _status_output(repo, capsys)
    assert rc == 1
    assert "\n  API docs:\n" in out
    assert "    pkg/core.py: not-updated — docs_src/core.md; undocumented: new_api\n" in out


def test_a_later_page_edit_in_the_range_counts_as_updated(repo: Path, capsys):
    _docs_repo(repo)
    _write(repo, "pkg/core.py", (repo / "pkg/core.py").read_text() + "\ndef new_api():\n    pass\n")
    sha = _commit(repo, "add new_api")
    _write(repo, "docs_src/core.md", "Call `run` or `new_api`.\n")
    _commit(repo, "document new_api")
    _, commits = ai.status(repo)
    assert [c.commit for c in commits] == [sha]
    [ds] = [d for d in commits[0].docs if d.module == "pkg/core.py"]
    assert ds.state == "updated" and ds.undocumented == []
    rc, out = _status_output(repo, capsys)
    assert rc == 1
    assert "API docs:" not in out


def test_an_earlier_page_edit_does_not_count_for_a_later_commit(repo: Path):
    _docs_repo(repo)
    _write(repo, "docs_src/core.md", "Call `run` to begin.\n")
    _commit(repo, "reword page")
    _write(repo, "pkg/core.py", (repo / "pkg/core.py").read_text() + "\ndef new_api():\n    pass\n")
    _commit(repo, "add new_api")
    _, commits = ai.status(repo)
    [ds] = [d for d in commits[-1].docs if d.module == "pkg/core.py"]
    assert ds.state == "not-updated"


def test_status_prints_no_page_for_a_new_module(repo: Path, capsys):
    _write(repo, "pkg/fresh.py", "def fn():\n    pass\n")
    _commit(repo, "add fresh")
    _, commits = ai.status(repo)
    [ds] = [d for d in commits[0].docs if d.module == "pkg/fresh.py"]
    assert ds.state == "no-page" and ds.added_module and ds.pages == []
    rc, out = _status_output(repo, capsys)
    assert rc == 1
    assert "  API docs:\n" in out
    assert "\n    pkg/fresh.py: no-page" in out
    assert "pkg/fresh.py: no-page —" not in out


def test_reviewed_commits_carry_no_docs(repo: Path, capsys):
    _write(repo, "pkg/fresh.py", "def fn():\n    pass\n")
    _commit(repo, "add fresh")
    assert ai.status(repo)[1][0].docs
    ai.record(repo, "reviewed")
    _commit(repo, "record review")
    _, commits = ai.status(repo)
    assert commits[0].reviewed and commits[0].docs == []
    rc, out = _status_output(repo, capsys)
    assert rc == 0
    assert "API docs:" not in out


def test_status_json_carries_every_docstatus(repo: Path, capsys):
    _docs_repo(repo)
    _write(repo, "pkg/core.py", "def run(a):\n    return a\n\nclass Tool:\n    def go(self):\n        pass\n")
    _commit(repo, "narrow run")
    _write(repo, "docs_src/core.md", "Call `run(a)` to start.\n")
    _commit(repo, "update page")
    capsys.readouterr()
    rc = ai.main(["--repo", str(repo), "status", "--json"])
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    docs = data["commits"][0]["docs"]
    assert docs == [{"module": "pkg/core.py", "pages": ["docs_src/core.md"], "state": "updated",
                     "added_module": False, "undocumented": [], "stale": [],
                     "public_api": False}]
