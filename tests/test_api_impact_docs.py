"""Tests for agentteams.api_impact's API-doc mapping: DocStatus, doc_pages and doc_status (plan api-doc-map, I2-2).

Each test builds a throwaway git repository, so page lookup runs against real ``ls-tree``/``ls-files`` output.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentteams import api_impact as ai
from tests.test_api_impact import _commit, _git, _write

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _write(tmp_path, "pkg/core.py", "def run():\n    pass\n")
    _commit(tmp_path, "base")
    return tmp_path


def _pages(repo: Path, pages: dict[str, str]) -> None:
    for rel, text in pages.items():
        _write(repo, rel, text)
    _commit(repo, "pages")


def _one(statuses: list[ai.DocStatus], module: str) -> ai.DocStatus:
    found = [s for s in statuses if s.module == module]
    assert len(found) == 1, statuses
    return found[0]


# --- DocStatus --------------------------------------------------------------

@pytest.mark.parametrize(("state", "added", "public", "undoc", "stale", "reportable", "blocking"), [
    ("no-page", False, False, [], [], False, False),
    ("no-page", True, True, [], [], True, True),
    ("no-page", True, False, [], [], False, False),   # new module without public names (rev 3)
    ("not-updated", False, False, [], [], True, True),
    ("updated", False, False, [], [], False, False),
    ("updated", False, False, ["x"], [], True, False),
    ("updated", False, False, [], ["y"], True, False),
    ("no-page", False, False, ["x"], [], True, False),
])
def test_docstatus_reportable_and_blocking(state, added, public, undoc, stale, reportable, blocking):
    ds = ai.DocStatus("pkg/m.py", [] if state == "no-page" else ["docs/m.md"], state, added, undoc, stale,
                      public)
    assert ds.reportable is reportable
    assert ds.blocking is blocking


def test_docstatus_list_defaults_are_not_shared():
    a = ai.DocStatus("a.py", [], "no-page", False)
    b = ai.DocStatus("b.py", [], "no-page", False)
    a.undocumented.append("x")
    a.stale.append("y")
    assert b.undocumented == [] and b.stale == []


# --- doc_pages: the name rule -------------------------------------------------

def test_dash_underscore_and_case_are_ignored(repo: Path):
    _pages(repo, {"docs/API-Impact.md": "x\n", "docs_src/api_impact.md": "x\n", "docs/api-impacts.md": "x\n"})
    assert ai.doc_pages(repo, "pkg/api_impact.py") == ["docs/API-Impact.md", "docs_src/api_impact.md"]


def test_init_maps_to_the_package_name(repo: Path):
    _pages(repo, {"docs/pkg.md": "x\n", "docs/__init__.md": "x\n"})
    assert ai.doc_pages(repo, "pkg/__init__.py") == ["docs/pkg.md"]


def test_root_init_main_tests_and_non_python_have_no_pages(repo: Path):
    _pages(repo, {"docs/__init__.md": "x\n", "docs/__main__.md": "x\n", "docs/test_core.md": "x\n",
                  "docs/core.md": "x\n", "docs/pkg.md": "x\n"})
    assert ai.doc_pages(repo, "__init__.py") == []
    assert ai.doc_pages(repo, "pkg/__main__.py") == []
    assert ai.doc_pages(repo, "__main__.py") == []
    assert ai.doc_pages(repo, "tests/test_core.py") == []
    assert ai.doc_pages(repo, "pkg/core.txt") == []


def test_excluded_page_locations(repo: Path):
    _pages(repo, {
        ".claude/agents/cleanup.md": "x\n",
        ".github/agents/cleanup.md": "x\n",
        "references/cleanup.md": "x\n",
        "agents/cleanup.md": "x\n",
        "examples/demo/expected/cleanup.md": "x\n",
        "expected/cleanup.md": "x\n",
        "tmp/cleanup.md": "x\n",
        "tests/cleanup.md": "x\n",
        "test/cleanup.md": "x\n",
        "workSummaries/cleanup.md": "x\n",
        "docs_src/cleanup.md": "x\n",
    })
    assert ai.doc_pages(repo, "agentteams/cleanup.py") == ["docs_src/cleanup.md"]


@pytest.mark.parametrize(("module", "page"), [
    ("pkg/index.py", "docs/index.md"),
    ("pkg/readme.py", "docs/README.md"),
    ("pkg/changelog.py", "docs/CHANGELOG.md"),
])
def test_generic_page_stems_never_match(repo: Path, module: str, page: str):
    _pages(repo, {page: "x\n"})
    assert ai.doc_pages(repo, module) == []


def test_non_markdown_pages_do_not_match(repo: Path):
    _pages(repo, {"docs/core.txt": "x\n", "docs/core.rst": "x\n"})
    assert ai.doc_pages(repo, "pkg/core.py") == []


def test_ref_empty_reads_the_index_and_head_reads_the_commit(repo: Path):
    _pages(repo, {"docs/other.md": "x\n"})
    _write(repo, "docs/core.md", "x\n")
    _git(repo, "add", "docs/core.md")
    assert ai.doc_pages(repo, "pkg/core.py", ref="") == ["docs/core.md"]
    assert ai.doc_pages(repo, "pkg/core.py", ref="HEAD") == []
    assert ai.doc_pages(repo, "pkg/core.py") == []  # default is HEAD


# --- doc_status ---------------------------------------------------------------

def test_added_symbol_not_mentioned_is_undocumented(repo: Path):
    _pages(repo, {"docs/core.md": "Call `run`.\n"})
    imp = ai.Impact(symbols_added=["pkg/core.py::new_api"])
    ds = _one(ai.doc_status(repo, imp, []), "pkg/core.py")
    assert ds.undocumented == ["new_api"]
    assert ds.pages == ["docs/core.md"] and ds.state == "not-updated"
    assert ds.reportable and ds.blocking and not ds.added_module


def test_added_symbol_mentioned_is_documented(repo: Path):
    _pages(repo, {"docs/core.md": "Call `new_api()`.\n"})
    imp = ai.Impact(symbols_added=["pkg/core.py::new_api"])
    assert _one(ai.doc_status(repo, imp, []), "pkg/core.py").undocumented == []


def test_mention_is_whole_word(repo: Path):
    _pages(repo, {"docs/core.md": "See foobar and xfoo.\n"})
    imp = ai.Impact(symbols_added=["pkg/core.py::foo"])
    assert _one(ai.doc_status(repo, imp, []), "pkg/core.py").undocumented == ["foo"]


def test_method_is_checked_by_its_last_component(repo: Path):
    _pages(repo, {"docs/core.md": "The spin() method.\n"})
    imp = ai.Impact(symbols_added=["pkg/core.py::Tool.spin", "pkg/core.py::Tool.halt"])
    assert _one(ai.doc_status(repo, imp, []), "pkg/core.py").undocumented == ["Tool.halt"]


def test_dunders_are_never_checked(repo: Path):
    _pages(repo, {"docs/core.md": "nothing\n"})
    imp = ai.Impact(symbols_added=["pkg/core.py::Tool.__init__", "pkg/core.py::__all__"],
                    symbols_removed=["pkg/core.py::Tool.__repr__"])
    ds = _one(ai.doc_status(repo, imp, []), "pkg/core.py")
    assert ds.undocumented == [] and ds.stale == []


def test_removed_name_still_mentioned_is_stale(repo: Path):
    _pages(repo, {"docs/core.md": "Call `run` to start.\n"})
    imp = ai.Impact(symbols_removed=["pkg/core.py::run", "pkg/core.py::other"])
    ds = _one(ai.doc_status(repo, imp, []), "pkg/core.py")
    assert ds.stale == ["run"]
    assert ds.undocumented == []


def test_removed_module_with_a_page_is_stale_by_key(repo: Path):
    _pages(repo, {"docs/old-mod.md": "old\n"})
    imp = ai.Impact(modules_removed=["pkg/old_mod.py"])
    ds = _one(ai.doc_status(repo, imp, []), "pkg/old_mod.py")
    assert ds.pages == ["docs/old-mod.md"]
    assert ds.stale == ["old_mod"]
    assert ds.added_module is False


def test_removed_module_without_a_page_is_not_stale(repo: Path):
    imp = ai.Impact(modules_removed=["pkg/gone.py"])
    for ds in ai.doc_status(repo, imp, []):
        assert ds.stale == []
        assert not ds.reportable


def test_renamed_module_adds_old_page_and_old_key_is_stale(repo: Path):
    _pages(repo, {"docs/core.md": "core\n", "docs/engine.md": "engine\n"})
    imp = ai.Impact(modules_renamed=[("pkg/core.py", "pkg/engine.py")])
    statuses = ai.doc_status(repo, imp, [])
    assert [s.module for s in statuses] == ["pkg/engine.py"]
    ds = statuses[0]
    assert ds.pages == ["docs/core.md", "docs/engine.md"]
    assert ds.stale == ["core"]
    assert ds.added_module is True


def test_renamed_module_without_old_page_has_no_stale_key(repo: Path):
    _pages(repo, {"docs/engine.md": "engine\n"})
    imp = ai.Impact(modules_renamed=[("pkg/core.py", "pkg/engine.py")])
    ds = _one(ai.doc_status(repo, imp, []), "pkg/engine.py")
    assert ds.pages == ["docs/engine.md"] and ds.stale == []


@pytest.mark.parametrize(("touched", "state"), [
    (["docs/core.md"], "updated"),
    (["pkg/core.py", "docs/other.md"], "not-updated"),
    ([], "not-updated"),
])
def test_state_follows_touched(repo: Path, touched: list[str], state: str):
    _pages(repo, {"docs/core.md": "run\n", "docs/other.md": "x\n"})
    imp = ai.Impact(symbols_changed=["pkg/core.py::run"])
    assert _one(ai.doc_status(repo, imp, touched), "pkg/core.py").state == state


def test_touched_accepts_any_iterable(repo: Path):
    _pages(repo, {"docs/core.md": "run\n"})
    imp = ai.Impact(symbols_changed=["pkg/core.py::run"])
    ds = _one(ai.doc_status(repo, imp, iter({"docs/core.md"})), "pkg/core.py")
    assert ds.state == "updated"


def test_no_page_state_and_added_module(repo: Path):
    _pages(repo, {"docs/other.md": "x\n"})
    imp = ai.Impact(modules_added=["pkg/fresh.py"], symbols_added=["pkg/fresh.py::make"],
                    symbols_changed=["pkg/core.py::run"])
    statuses = ai.doc_status(repo, imp, [])
    fresh = _one(statuses, "pkg/fresh.py")
    assert fresh.state == "no-page" and fresh.pages == [] and fresh.added_module is True
    assert fresh.public_api is True and fresh.undocumented == []
    assert fresh.blocking and fresh.reportable
    core = _one(statuses, "pkg/core.py")
    assert core.state == "no-page" and core.added_module is False
    assert not core.blocking and not core.reportable


def test_each_module_once_and_sorted(repo: Path):
    _pages(repo, {"docs/core.md": "run\n"})
    imp = ai.Impact(modules_added=["pkg/zeta.py"],
                    symbols_added=["pkg/zeta.py::z", "pkg/alpha.py::a"],
                    symbols_changed=["pkg/core.py::run"],
                    symbols_removed=["pkg/core.py::old"])
    modules = [s.module for s in ai.doc_status(repo, imp, [])]
    assert modules == ["pkg/alpha.py", "pkg/core.py", "pkg/zeta.py"]


def test_undocumented_and_stale_are_sorted(repo: Path):
    _pages(repo, {"docs/core.md": "zed and alpha\n"})
    imp = ai.Impact(symbols_added=["pkg/core.py::b", "pkg/core.py::a"],
                    symbols_removed=["pkg/core.py::zed", "pkg/core.py::alpha"])
    ds = _one(ai.doc_status(repo, imp, []), "pkg/core.py")
    assert ds.undocumented == ["a", "b"]
    assert ds.stale == ["alpha", "zed"]


def test_ref_empty_reads_page_text_from_the_index(repo: Path):
    _pages(repo, {"docs/core.md": "nothing\n"})
    _write(repo, "docs/core.md", "Call new_api.\n")
    _git(repo, "add", "docs/core.md")
    imp = ai.Impact(symbols_added=["pkg/core.py::new_api"])
    assert _one(ai.doc_status(repo, imp, [], ref=""), "pkg/core.py").undocumented == []
    assert _one(ai.doc_status(repo, imp, [], ref="HEAD"), "pkg/core.py").undocumented == ["new_api"]


# --- contract rev 3 (closeout audit round 2) ----------------------------------

def test_new_module_without_public_names_or_private_is_not_blocking(repo: Path):
    imp = ai.Impact(modules_added=["pkg/_helpers.py", "pkg/empty.py"], symbols_added=["pkg/_helpers.py::tool"])
    statuses = ai.doc_status(repo, imp, [])
    for module in ("pkg/_helpers.py", "pkg/empty.py"):
        ds = _one(statuses, module)
        assert ds.state == "no-page" and ds.public_api is False
        assert not ds.blocking and not ds.reportable


def test_existing_module_without_page_reports_nothing(repo: Path):
    imp = ai.Impact(symbols_added=["pkg/core.py::extra"])
    ds = _one(ai.doc_status(repo, imp, []), "pkg/core.py")
    assert ds.state == "no-page" and ds.undocumented == []
    assert not ds.reportable


def test_page_staged_for_deletion_still_maps_and_counts_as_touched(repo: Path):
    _pages(repo, {"docs/core.md": "run\n"})
    _git(repo, "rm", "-q", "docs/core.md")
    imp = ai.Impact(symbols_added=["pkg/core.py::extra"])
    ds = _one(ai.doc_status(repo, imp, ["docs/core.md"], ref=""), "pkg/core.py")
    assert ds.pages == ["docs/core.md"] and ds.state == "updated"
    assert ds.undocumented == ["extra"] and ds.reportable and not ds.blocking


def test_symlinked_page_is_not_a_candidate(repo: Path):
    _write(repo, "docs/real.md", "run\n")
    (repo / "docs" / "core.md").symlink_to("real.md")
    _commit(repo, "symlink")
    assert ai.doc_pages(repo, "pkg/core.py") == []
    assert ai.doc_pages(repo, "pkg/core.py", ref="") == []


def test_main_resolves_repo_top_level_from_subdirectory(repo: Path, capsys):
    _pages(repo, {"docs/core.md": "run\n"})
    _write(repo, "pkg/core.py", "def run():\n    pass\ndef extra():\n    pass\n")
    _git(repo, "add", "pkg/core.py")
    assert ai.main(["--pre-commit", "--repo", str(repo / "pkg")]) == 0
    assert "pkg/core.py: docs/core.md not updated (undocumented: extra)" in capsys.readouterr().err
