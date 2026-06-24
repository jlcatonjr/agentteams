"""Tests for scripts/check_api_doc_parity.py — api-reference ↔ module parity.

The load-bearing invariant is ``no STALE_PAGE``: every page in
``docs_src/api-reference/`` must map to a module that still exists. This catches
a doc page left behind after a module is renamed/deleted. Coverage gaps (a public
module with no page) are deliberately advisory, not enforced here, because the
current tree carries several intentional gaps — enforcing them would be a false
CI break (see the script docstring).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_api_doc_parity.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_api_doc_parity", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the @dataclass decorator can resolve the module
    # namespace (required under `from __future__ import annotations`).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def parity():
    return _load_module()


def test_script_exists(parity):
    assert _SCRIPT_PATH.is_file()


def test_no_stale_pages(parity):
    """Hard invariant: no reference page documents a deleted/renamed module."""
    result = parity.compute_parity()
    assert result.stale_pages == [], (
        "api-reference pages with no backing module (rename the module's page or "
        f"remove the page): {result.stale_pages}"
    )


def test_some_pages_are_documented(parity):
    """Sanity: the mapper actually resolves real pages (guards a broken normalizer)."""
    result = parity.compute_parity()
    assert len(result.documented) > 10


def test_stale_page_is_detected(parity, tmp_path):
    """Positive case: a page with no backing module is classified STALE_PAGE."""
    docs = tmp_path / "api-reference"
    docs.mkdir()
    (docs / "zzz-deleted-module.md").write_text("# gone\n", encoding="utf-8")
    (docs / "analyze.md").write_text("# analyze\n", encoding="utf-8")  # real module
    result = parity.compute_parity(docs_dir=docs)
    assert "zzz-deleted-module" in result.stale_pages
    assert "analyze" in result.documented


def test_pkg_dir_is_honored_for_stale_detection(parity, tmp_path):
    """Injecting an empty package dir makes every real page read as STALE_PAGE
    (guards against _module_exists ignoring its pkg_dir argument)."""
    empty_pkg = tmp_path / "agentteams"
    empty_pkg.mkdir()
    result = parity.compute_parity(pkg_dir=empty_pkg)
    assert result.documented == []
    assert len(result.stale_pages) > 10


def test_check_flag_passes_on_current_tree(parity):
    """`--check` (STALE_PAGE only) must exit 0 on the committed tree."""
    assert parity.main(["--check"]) == 0


def test_strict_flag_is_available(parity):
    """`--strict` returns an int exit code (1 while coverage gaps exist)."""
    rc = parity.main(["--strict", "--json"])
    assert rc in (0, 1)


def test_dash_underscore_normalization(parity):
    """A dashed page stem normalizes to an underscored module name."""
    assert parity._module_name_for_page("plan-steps-todo") == "plan_steps_todo"
    assert parity._module_exists("plan_steps_todo") is True


def test_mcp_pages_now_resolve(parity):
    """mcp-detect.md / mcp-emit.md were added with the specified-server work;
    confirm they are counted as documented, not stale or gaps."""
    result = parity.compute_parity()
    assert "mcp-detect" in result.documented
    assert "mcp-emit" in result.documented
    assert "mcp_detect" not in result.coverage_gaps
    assert "mcp_emit" not in result.coverage_gaps
