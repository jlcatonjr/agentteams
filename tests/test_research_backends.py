"""Tests for agentteams.research.backends — the search fallback chain.

Motivating defect (measured 2026-07-30): search had exactly one backend, and long
multi-concept queries were challenged deterministically. The chain's whole purpose is that a
challenge on one endpoint is not the end of the attempt, so these tests focus on the chain
BOUNDARY conditions — availability gating, fallthrough, and the zero-configuration guarantee —
rather than re-testing HTML parsing already covered in test_research_search.py.

No test here performs live network I/O.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentteams.research.backends import (
    BRAVE_KEY_ENV,
    CHALLENGE_STATUS,
    SEARXNG_URL_ENV,
    available_backends,
    backend_names,
    resolve_redirect,
)


class _FakeResp:
    def __init__(self, status_code: int, text: str = "", payload: object = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError("fixture should not use error statuses here")

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture(autouse=True)
def _clear_backend_env(monkeypatch):
    """Every test starts from the zero-configuration state.

    Without this, a developer machine with AGENTTEAMS_BRAVE_API_KEY exported would silently
    change which backends are in the chain and make these assertions machine-dependent.
    """
    for var in (SEARXNG_URL_ENV, BRAVE_KEY_ENV):
        monkeypatch.delenv(var, raising=False)


def test_zero_configuration_still_yields_a_real_chain():
    """The core guarantee: a fallback that requires setup is not a fallback.

    An expansion whose benefit is opt-in does not remediate the reported defect, so the two
    key-free DuckDuckGo endpoints must both be present with no environment configuration.
    """
    names = backend_names()
    assert names == ["duckduckgo", "ddg_lite"]
    assert len(names) >= 2, "an unconfigured install must have somewhere to fall back TO"


def test_keyed_backends_are_absent_until_configured():
    assert "brave" not in backend_names()
    assert "searxng" not in backend_names()


def test_brave_joins_the_chain_when_a_key_is_present(monkeypatch):
    monkeypatch.setenv(BRAVE_KEY_ENV, "test-key")
    assert "brave" in backend_names()


def test_searxng_joins_the_chain_when_a_url_is_present(monkeypatch):
    monkeypatch.setenv(SEARXNG_URL_ENV, "https://searx.example.org")
    assert "searxng" in backend_names()


def test_configured_backends_come_after_the_free_ones(monkeypatch):
    """An operator who configures a paid backend should not pay for every query."""
    monkeypatch.setenv(BRAVE_KEY_ENV, "test-key")
    names = backend_names()
    assert names.index("duckduckgo") < names.index("brave")
    assert names.index("ddg_lite") < names.index("brave")


def test_whitespace_only_env_does_not_enable_a_backend(monkeypatch):
    """A blank export is a common accident; it must not put a broken backend in the chain."""
    monkeypatch.setenv(BRAVE_KEY_ENV, "   ")
    monkeypatch.setenv(SEARXNG_URL_ENV, "  ")
    assert backend_names() == ["duckduckgo", "ddg_lite"]


def test_challenge_is_reported_distinctly_from_empty():
    """The distinction the whole design rests on: blocked != nothing matched."""
    ddg = available_backends()[0]
    with patch("agentteams.research.backends.httpx.get",
               return_value=_FakeResp(CHALLENGE_STATUS, "<html>challenge</html>")):
        hits, challenged = ddg.search("q", 5, 1.0)
    assert hits == [] and challenged is True

    with patch("agentteams.research.backends.httpx.get",
               return_value=_FakeResp(200, "<html>nothing</html>")):
        hits, challenged = ddg.search("q", 5, 1.0)
    assert hits == [] and challenged is False


def test_transport_failure_degrades_to_empty_and_never_raises():
    import httpx

    ddg = available_backends()[0]
    with patch("agentteams.research.backends.httpx.get",
               side_effect=httpx.ConnectError("network down")):
        hits, challenged = ddg.search("q", 5, 1.0)
    assert hits == [] and challenged is False


def test_lite_backend_parses_its_own_distinct_markup():
    """ddg_lite is only useful as a fallback if it can actually parse its own renderer."""
    lite = next(b for b in available_backends() if b.name == "ddg_lite")
    html = (
        '<a class="result-link" href="https://example.com/x">X Title</a>'
        '<td class="result-snippet">x snippet</td>'
    )
    with patch("agentteams.research.backends.httpx.get", return_value=_FakeResp(200, html)):
        hits, challenged = lite.search("q", 5, 1.0)
    assert [h.title for h in hits] == ["X Title"]
    assert hits[0].url == "https://example.com/x"
    assert challenged is False


def test_searxng_handles_an_instance_with_json_disabled(monkeypatch):
    """Many public instances answer with HTML instead of JSON. That is a miss, not a crash."""
    monkeypatch.setenv(SEARXNG_URL_ENV, "https://searx.example.org")
    backend = next(b for b in available_backends() if b.name == "searxng")
    with patch("agentteams.research.backends.httpx.get",
               return_value=_FakeResp(200, "<html>not json</html>")):
        hits, challenged = backend.search("q", 5, 1.0)
    assert hits == [] and challenged is False


def test_searxng_parses_a_json_response(monkeypatch):
    monkeypatch.setenv(SEARXNG_URL_ENV, "https://searx.example.org")
    backend = next(b for b in available_backends() if b.name == "searxng")
    payload = {"results": [{"title": "T", "url": "https://e.com/1", "content": "C"}]}
    with patch("agentteams.research.backends.httpx.get",
               return_value=_FakeResp(200, payload=payload)):
        hits, _ = backend.search("q", 5, 1.0)
    assert [(h.title, h.url, h.snippet) for h in hits] == [("T", "https://e.com/1", "C")]


def test_brave_parses_a_json_response(monkeypatch):
    monkeypatch.setenv(BRAVE_KEY_ENV, "test-key")
    backend = next(b for b in available_backends() if b.name == "brave")
    payload = {"web": {"results": [{"title": "B", "url": "https://e.com/b", "description": "D"}]}}
    with patch("agentteams.research.backends.httpx.get",
               return_value=_FakeResp(200, payload=payload)) as mock_get:
        hits, _ = backend.search("q", 5, 1.0)
    assert [(h.title, h.snippet) for h in hits] == [("B", "D")]
    # The key must travel as a header, never as a query parameter (query strings land in
    # server logs and browser history in a way headers do not).
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["X-Subscription-Token"] == "test-key"
    assert "test-key" not in str(kwargs.get("params", {}))


def test_brave_result_without_a_url_is_dropped(monkeypatch):
    monkeypatch.setenv(BRAVE_KEY_ENV, "test-key")
    backend = next(b for b in available_backends() if b.name == "brave")
    payload = {"web": {"results": [{"title": "no url"}, {"title": "ok", "url": "https://e.com"}]}}
    with patch("agentteams.research.backends.httpx.get",
               return_value=_FakeResp(200, payload=payload)):
        hits, _ = backend.search("q", 5, 1.0)
    assert [h.title for h in hits] == ["ok"]


@pytest.mark.parametrize(
    "href,expected",
    [
        ("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa", "https://example.com/a"),
        ("https://example.com/plain", "https://example.com/plain"),
        ("//duckduckgo.com/l/?nothing=1", "//duckduckgo.com/l/?nothing=1"),
    ],
)
def test_resolve_redirect(href, expected):
    assert resolve_redirect(href) == expected
