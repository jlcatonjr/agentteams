"""Tests for agentteams/enrich/_tools.py's network-fetch tiers (PyPI, npm) and
build_tool_catalog's resolution chain (unified static catalog -> PyPI -> npm).
Prior to tmp/by-week/2026-W30/tool-doc-catalog-remediation.plan.md this module had
zero direct test coverage."""
from __future__ import annotations

import json
import urllib.error

from agentteams.enrich._tools import (
    _fetch_npm_metadata,
    _fetch_pypi_metadata,
    _get_docs_url,
    build_tool_catalog,
)


class _FakeResponse:
    """Minimal stand-in for the context-manager object urllib.request.urlopen returns."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# build_tool_catalog: unified static catalog takes priority over any network call
# ---------------------------------------------------------------------------

def test_build_tool_catalog_prefers_static_catalog_over_network(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("network fetch must not be attempted for a known package")

    monkeypatch.setattr("agentteams.enrich._tools.urllib.request.urlopen", _boom)
    catalog = build_tool_catalog(["boto3"], fetch_pypi=True)
    assert catalog["boto3"]["docs_url"].startswith("https://boto3.amazonaws.com")


def test_build_tool_catalog_static_only_mode_returns_empty_for_unknown():
    catalog = build_tool_catalog(["totally-unknown-xyz"], fetch_pypi=False)
    assert catalog["totally-unknown-xyz"] == {
        "docs_url": "", "api_surface": "", "common_patterns": "",
    }


def test_build_tool_catalog_falls_back_to_pypi_for_unknown_package(monkeypatch):
    monkeypatch.setattr(
        "agentteams.enrich._tools.urllib.request.urlopen",
        lambda req, timeout=6: _FakeResponse(
            {"info": {"project_urls": {"Documentation": "https://pypi-only.dev"}}}
        ),
    )
    catalog = build_tool_catalog(["some-unknown-pypi-package"], fetch_pypi=True)
    assert catalog["some-unknown-pypi-package"]["docs_url"] == "https://pypi-only.dev"


def test_build_tool_catalog_pypi_to_npm_fallback_merges_not_replaces(monkeypatch):
    """Regression test (post-implementation audit finding,
    tmp/by-week/2026-W30/tool-doc-catalog-remediation.plan.md): PyPI can return a
    real api_surface (from its summary field) alongside an empty docs_url (no
    Documentation/Homepage/Source/Repository project URL at all). Falling through
    to npm for docs_url must not discard that api_surface — npm's own (empty, for
    a Python-only package) result must only fill the gap, not replace the dict."""
    def _fake_urlopen(req, timeout=6):
        if "pypi.org" in req.full_url:
            return _FakeResponse({"info": {"summary": "A real PyPI-only package."}})
        return _FakeResponse({"dist-tags": {"latest": "1.0.0"}, "versions": {"1.0.0": {}}})

    monkeypatch.setattr("agentteams.enrich._tools.urllib.request.urlopen", _fake_urlopen)
    catalog = build_tool_catalog(["pypi-only-no-docs-url"], fetch_pypi=True)
    assert catalog["pypi-only-no-docs-url"]["api_surface"] == "A real PyPI-only package."
    assert catalog["pypi-only-no-docs-url"]["docs_url"] == ""


# ---------------------------------------------------------------------------
# npm fetch — mocked network, mirrors the existing PyPI fetch's structure and
# CH-24-narrow exception handling; covers scoped packages (@scope/name).
# ---------------------------------------------------------------------------

def test_fetch_npm_metadata_resolves_homepage(monkeypatch):
    payload = {
        "dist-tags": {"latest": "1.2.3"},
        "versions": {
            "1.2.3": {
                "homepage": "https://example.com/docs",
                "description": "An example npm package.",
            },
        },
    }
    monkeypatch.setattr(
        "agentteams.enrich._tools.urllib.request.urlopen",
        lambda req, timeout=6: _FakeResponse(payload),
    )
    result = _fetch_npm_metadata("some-package")
    assert result["docs_url"] == "https://example.com/docs"
    assert result["api_surface"] == "An example npm package."


def test_fetch_npm_metadata_falls_back_to_repository_url(monkeypatch):
    payload = {
        "dist-tags": {"latest": "1.0.0"},
        "versions": {
            "1.0.0": {"repository": {"url": "git+https://github.com/org/repo.git"}},
        },
    }
    monkeypatch.setattr(
        "agentteams.enrich._tools.urllib.request.urlopen",
        lambda req, timeout=6: _FakeResponse(payload),
    )
    result = _fetch_npm_metadata("some-package")
    assert result["docs_url"] == "https://github.com/org/repo"


def test_fetch_npm_metadata_scoped_package_builds_correct_registry_url(monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout=6):
        captured["url"] = req.full_url
        return _FakeResponse({
            "dist-tags": {"latest": "1.0.0"},
            "versions": {"1.0.0": {"homepage": "https://x.dev"}},
        })

    monkeypatch.setattr("agentteams.enrich._tools.urllib.request.urlopen", _fake_urlopen)
    result = _fetch_npm_metadata("@typescript-eslint/parser")
    assert captured["url"] == "https://registry.npmjs.org/@typescript-eslint/parser"
    assert result["docs_url"] == "https://x.dev"


def test_fetch_npm_metadata_network_error_returns_empty(monkeypatch):
    def _boom(req, timeout=6):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("agentteams.enrich._tools.urllib.request.urlopen", _boom)
    assert _fetch_npm_metadata("some-package") == {
        "docs_url": "", "api_surface": "", "common_patterns": "",
    }


def test_fetch_npm_metadata_malformed_json_returns_empty(monkeypatch):
    class _BadResponse:
        def read(self):
            return b"not json"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "agentteams.enrich._tools.urllib.request.urlopen",
        lambda req, timeout=6: _BadResponse(),
    )
    assert _fetch_npm_metadata("some-package") == {
        "docs_url": "", "api_surface": "", "common_patterns": "",
    }


def test_fetch_npm_metadata_rejects_names_with_spaces():
    assert _fetch_npm_metadata("not a real package") == {
        "docs_url": "", "api_surface": "", "common_patterns": "",
    }


def test_fetch_npm_metadata_rejects_empty_name():
    assert _fetch_npm_metadata("") == {
        "docs_url": "", "api_surface": "", "common_patterns": "",
    }


# ---------------------------------------------------------------------------
# _get_docs_url resolution chain: unified static catalog -> PyPI -> npm
# ---------------------------------------------------------------------------

def test_get_docs_url_uses_static_catalog_first(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("network fetch must not be attempted for a known package")

    monkeypatch.setattr("agentteams.enrich._tools.urllib.request.urlopen", _boom)
    assert _get_docs_url("boto3").startswith("https://boto3.amazonaws.com")


def test_get_docs_url_falls_through_pypi_to_npm(monkeypatch):
    def _fake_urlopen(req, timeout=6):
        if "pypi.org" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)
        return _FakeResponse({
            "dist-tags": {"latest": "1.0.0"},
            "versions": {"1.0.0": {"homepage": "https://npm-only.dev"}},
        })

    monkeypatch.setattr("agentteams.enrich._tools.urllib.request.urlopen", _fake_urlopen)
    assert _get_docs_url("some-npm-only-package") == "https://npm-only.dev"


def test_get_docs_url_empty_when_no_tier_resolves(monkeypatch):
    def _boom(req, timeout=6):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("agentteams.enrich._tools.urllib.request.urlopen", _boom)
    assert _get_docs_url("totally-unresolvable-xyz") == ""
