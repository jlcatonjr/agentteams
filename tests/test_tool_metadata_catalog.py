"""Tests for agentteams/tool_metadata_catalog.py — the unified static tool-metadata
catalog that replaced three drifted-apart catalogs (analyze.py's _KNOWN_TOOL_METADATA
and enrich/_tools.py's _TOOL_CATALOG + _CANONICAL_DOCS).
See tmp/by-week/2026-W30/tool-doc-catalog-remediation.plan.md."""
from __future__ import annotations

import pytest

from agentteams.tool_metadata_catalog import (
    TOOL_METADATA_CATALOG,
    get_tool_metadata,
    is_known_tool,
    normalize_tool_key,
)


def test_every_entry_has_all_three_fields():
    for key, entry in TOOL_METADATA_CATALOG.items():
        assert set(entry.keys()) == {"docs_url", "api_surface", "common_patterns"}, key
        assert entry["docs_url"], f"{key} has no docs_url"


def test_normalize_tool_key_is_alnum_lowercase_only():
    assert normalize_tool_key("D3.js") == "d3js"
    assert normalize_tool_key("arp-scan") == "arpscan"
    assert normalize_tool_key("SQLAlchemy") == "sqlalchemy"
    assert normalize_tool_key("pandas-datareader") == "pandasdatareader"


@pytest.mark.parametrize(
    "spelling",
    ["D3.js", "d3js", "d3.js", "D3JS"],
)
def test_spelling_variants_of_the_same_tool_resolve_identically(spelling):
    assert get_tool_metadata(spelling)["docs_url"] == "https://d3js.org/api"


def test_get_tool_metadata_unknown_returns_empty_dict():
    assert get_tool_metadata("totally-unknown-xyz-package") == {}


def test_is_known_tool():
    assert is_known_tool("boto3") is True
    assert is_known_tool("BOTO3") is True
    assert is_known_tool("totally-unknown-xyz-package") is False


# ---------------------------------------------------------------------------
# Packages previously reachable ONLY via the --enrich-gated catalogs
# (_TOOL_CATALOG / _CANONICAL_DOCS) — now unconditionally known.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,expected_docs_url",
    [
        ("boto3", "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/index.html"),
        ("requests", "https://requests.readthedocs.io/en/latest/api/"),
        ("sqlalchemy", "https://docs.sqlalchemy.org/en/latest/"),
        ("fastapi", "https://fastapi.tiangolo.com/reference/"),
        ("scikit-learn", "https://scikit-learn.org/stable/api/index.html"),
    ],
)
def test_enrich_only_packages_are_now_in_the_unconditional_catalog(name, expected_docs_url):
    assert get_tool_metadata(name)["docs_url"] == expected_docs_url


# ---------------------------------------------------------------------------
# The 5 tools where _KNOWN_TOOL_METADATA and _TOOL_CATALOG disagreed on
# docs_url — _TOOL_CATALOG's value must win (Design decision 1's tiebreak).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,tool_catalog_url,discarded_known_tool_metadata_url",
    [
        ("matplotlib", "https://matplotlib.org/stable/api/", "https://matplotlib.org/stable/contents.html"),
        ("numpy", "https://numpy.org/doc/stable/reference/", "https://numpy.org/doc/stable/"),
        ("pandas", "https://pandas.pydata.org/docs/reference/", "https://pandas.pydata.org/docs/"),
        (
            "pandas-datareader",
            "https://pandas-datareader.readthedocs.io/en/latest/",
            "https://pydata.github.io/pandas-datareader/",
        ),
        ("plotly", "https://plotly.com/python-api-reference/", "https://plotly.com/python/"),
    ],
)
def test_conflicting_overlap_prefers_tool_catalog_value(
    name, tool_catalog_url, discarded_known_tool_metadata_url
):
    resolved = get_tool_metadata(name)["docs_url"]
    assert resolved == tool_catalog_url
    assert resolved != discarded_known_tool_metadata_url


# ---------------------------------------------------------------------------
# Tools unique to _KNOWN_TOOL_METADATA (gap-filled, not dropped in the merge).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["linearmodels", "sqlite"])
def test_known_tool_metadata_only_entries_survived_the_merge(name):
    entry = get_tool_metadata(name)
    assert entry["docs_url"]
    assert entry["api_surface"]
    assert entry["common_patterns"]


def test_pytest_entry_added_and_matches_self_build_brief():
    entry = get_tool_metadata("pytest")
    assert entry["docs_url"] == "https://docs.pytest.org/en/stable/"
    assert "pytest.fixture" in entry["api_surface"]
    assert "conftest.py" in entry["common_patterns"]


# ---------------------------------------------------------------------------
# Both consumers (the unconditional analyze.py path and the opt-in
# enrich/_tools.py path) must resolve an overlapping tool identically —
# they now share one source of truth instead of two that could drift apart.
# ---------------------------------------------------------------------------

def test_analyze_and_enrich_resolve_the_same_tool_identically():
    from agentteams.analyze import _merge_known_tool_metadata
    from agentteams.enrich._tools import build_tool_catalog

    via_analyze = _merge_known_tool_metadata({"name": "matplotlib"})["docs_url"]
    via_enrich = build_tool_catalog(["matplotlib"], fetch_pypi=False)["matplotlib"]["docs_url"]
    assert via_analyze == via_enrich == "https://matplotlib.org/stable/api/"
