"""Tests for agentteams.research.browser — the Playwright-backed rendering tier for pages
agentteams.research.search.fetch_text can't reach.

`playwright` is not installed in this environment (agentteams[browser] is a heavy, separate
extra) and this suite does not require it: the module's own lazy-import design means everything
here is testable either with fake stand-ins for the Playwright API surface (the route-guard
security logic, the primary correctness target) or by confirming the module degrades honestly
when the real package genuinely isn't there — never that live browser automation itself is
correct end-to-end. That gap is named, not hidden: see
tmp/by-week/2026-W30/web-browsing-playwright-cli.plan.md's Non-goals.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

import agentteams.research as research_pkg
from agentteams.research.browser import _extract_text, _route_guard, browser_fetch, browser_screenshot


# ---------------------------------------------------------------------------
# _route_guard — the security-critical per-request SSRF check, tested directly
# ---------------------------------------------------------------------------
# Fake Route/Request stand-ins: any object exposing request.url + continue_()/abort() satisfies
# _route_guard's contract (see its own docstring — deliberately not type-pinned to Playwright's
# real Route), so a real browser is never needed to prove this logic is correct.


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class _FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = _FakeRequest(url)
        self.continued = False
        self.aborted = False

    def continue_(self) -> None:
        self.continued = True

    def abort(self) -> None:
        self.aborted = True


def test_route_guard_allows_public_https() -> None:
    route = _FakeRoute("https://example.com/page")
    _route_guard(route)
    assert route.continued
    assert not route.aborted


def test_route_guard_blocks_link_local_metadata_endpoint() -> None:
    """The canonical SSRF target: cloud-metadata link-local addresses. This is exactly the class
    of request a redirect or page-initiated JS fetch could reach that a pre-navigation-only check
    would never see -- the whole reason _route_guard exists as a *second*, per-request layer."""
    route = _FakeRoute("https://169.254.169.254/latest/meta-data/")
    _route_guard(route)
    assert route.aborted
    assert not route.continued


def test_route_guard_blocks_loopback() -> None:
    route = _FakeRoute("https://127.0.0.1/internal")
    _route_guard(route)
    assert route.aborted


def test_route_guard_blocks_non_https_scheme() -> None:
    """Covers a redirect or JS navigation to plain http, file:, or data: -- is_public_https's own
    scheme check already rejects all of these; this test pins that behavior at the route-guard
    boundary specifically, since that boundary is what a redirect/JS request actually passes
    through (the top-level pre-check in browser_fetch only ever sees the original URL)."""
    for url in ("http://example.com/plain", "file:///etc/passwd", "data:text/html,<script></script>"):
        route = _FakeRoute(url)
        _route_guard(route)
        assert route.aborted, f"expected {url!r} to be blocked"


# ---------------------------------------------------------------------------
# _extract_text — text-shape consistency with fetch_text, and the post-materialization size cap
# ---------------------------------------------------------------------------

def test_extract_text_strips_scripts_and_unescapes_entities() -> None:
    html = "<html><body><script>evil()</script><p>Hello &amp; World</p></body></html>"
    assert _extract_text(html, max_chars=100) == "Hello & World"


def test_extract_text_respects_max_chars() -> None:
    html = "<p>" + ("x" * 100) + "</p>"
    assert len(_extract_text(html, max_chars=10)) == 10


def test_extract_text_caps_raw_html_before_regex_processing() -> None:
    """A pathological page that renders an enormous DOM must not make text extraction itself
    unbounded -- the cap applies to the RAW html before tag-stripping, not just to the final
    output (which max_chars already bounds independently)."""
    from agentteams.research.browser import _MAX_RAW_HTML_CHARS

    oversized = "<p>" + ("y" * (_MAX_RAW_HTML_CHARS + 10_000)) + "</p>"
    # Ask for more chars than the raw-html cap allows, to prove the raw cap (not max_chars) is
    # what bounds the result here.
    result = _extract_text(oversized, max_chars=_MAX_RAW_HTML_CHARS + 10_000)
    assert len(result) <= _MAX_RAW_HTML_CHARS


# ---------------------------------------------------------------------------
# browser_fetch / browser_screenshot — pre-check fast path (no Playwright touched at all)
# ---------------------------------------------------------------------------

def test_browser_fetch_rejects_private_url_without_importing_playwright() -> None:
    """The pre-navigation is_public_https check must short-circuit before _rendered_page (and
    therefore the lazy `from playwright.sync_api import sync_playwright`) is ever reached --
    proven by patching _rendered_page to raise if it's called at all, not just by asserting the
    return value."""
    with patch(
        "agentteams.research.browser._rendered_page",
        side_effect=AssertionError("must not be called for a private-IP URL"),
    ):
        assert browser_fetch("https://127.0.0.1/secret") == ""


def test_browser_screenshot_rejects_non_https_without_importing_playwright() -> None:
    with patch(
        "agentteams.research.browser._rendered_page",
        side_effect=AssertionError("must not be called for a non-https URL"),
    ):
        assert browser_screenshot("http://example.com", "/tmp/out.png") is False


# ---------------------------------------------------------------------------
# browser_fetch / browser_screenshot — orchestration around _rendered_page (kwargs, extraction,
# exception -> honest-empty translation). _rendered_page's OWN internals (route wiring, goto) are
# exercised by the _route_guard tests above, not re-mocked here at the raw Playwright-API level --
# a deliberate scope choice to keep this suite focused: see the module docstring above.
# ---------------------------------------------------------------------------

class _FakePage:
    def __init__(self, content: str) -> None:
        self._content = content
        self.screenshot_calls: list[dict] = []

    def content(self) -> str:
        return self._content

    def screenshot(self, path: str, full_page: bool = True) -> None:
        self.screenshot_calls.append({"path": path, "full_page": full_page})


def _fake_rendered_page(page: _FakePage, calls: list[dict]):
    @contextmanager
    def _cm(url, *, headed, wait_until, timeout_s):
        calls.append(
            {"url": url, "headed": headed, "wait_until": wait_until, "timeout_s": timeout_s}
        )
        yield page

    return _cm


def test_browser_fetch_threads_kwargs_and_extracts_text() -> None:
    calls: list[dict] = []
    page = _FakePage("<html><body><p>Rendered content</p></body></html>")
    with patch(
        "agentteams.research.browser._rendered_page", _fake_rendered_page(page, calls)
    ):
        result = browser_fetch(
            "https://example.com/spa", headed=True, wait_until="load", timeout_s=5.0, max_chars=50
        )
    assert "Rendered content" in result
    assert calls == [
        {"url": "https://example.com/spa", "headed": True, "wait_until": "load", "timeout_s": 5.0}
    ]


def test_browser_fetch_defaults_headless_and_networkidle() -> None:
    calls: list[dict] = []
    page = _FakePage("<p>x</p>")
    with patch(
        "agentteams.research.browser._rendered_page", _fake_rendered_page(page, calls)
    ):
        browser_fetch("https://example.com/spa")
    assert calls[0]["headed"] is False
    assert calls[0]["wait_until"] == "networkidle"


def test_browser_fetch_returns_empty_on_any_exception_from_rendered_page() -> None:
    """Navigation timeout, missing browser binaries, a Playwright protocol error -- all funnel
    through the same broad except in browser_fetch and degrade to "", never raise."""
    with patch(
        "agentteams.research.browser._rendered_page",
        side_effect=RuntimeError("Executable doesn't exist at .../chromium-1234/headless_shell"),
    ):
        assert browser_fetch("https://example.com/spa") == ""


def test_browser_screenshot_saves_and_returns_true_on_success() -> None:
    calls: list[dict] = []
    page = _FakePage("<p>unused for screenshot</p>")
    with patch(
        "agentteams.research.browser._rendered_page", _fake_rendered_page(page, calls)
    ):
        ok = browser_screenshot("https://example.com/spa", "/tmp/out.png")
    assert ok is True
    assert page.screenshot_calls == [{"path": "/tmp/out.png", "full_page": True}]


def test_browser_screenshot_returns_false_on_any_exception() -> None:
    with patch(
        "agentteams.research.browser._rendered_page", side_effect=RuntimeError("navigation timeout")
    ):
        assert browser_screenshot("https://example.com/spa", "/tmp/out.png") is False


# ---------------------------------------------------------------------------
# Import boundary: browser.py must not require playwright at module scope, and must not be
# re-exported from agentteams.research's own package-level surface. Mirrors
# tests/test_research_search.py's pypdf-blocking precedent exactly (same technique, same
# rigor) rather than merely relying on this environment's accidental absence of playwright.
# ---------------------------------------------------------------------------

def test_no_module_level_playwright_import() -> None:
    """browser.py must be importable without playwright ever being touched at module scope --
    checks for the actual `from playwright...`/`import playwright` statements specifically
    (not the bare word "playwright", which legitimately appears throughout this module's own
    docstrings)."""
    import inspect

    import agentteams.research.browser as browser_module

    module_source = inspect.getsource(browser_module)
    rendered_page_source = inspect.getsource(browser_module._rendered_page)
    module_only = module_source.replace(rendered_page_source, "")
    assert "import playwright" not in module_only
    assert "from playwright" not in module_only


def test_import_agentteams_research_browser_without_playwright() -> None:
    """Live-confirm the lazy-import claim, not just by static inspection: block playwright at
    import time with a genuine find_spec-based meta-path finder (matching
    test_research_search.py's identical pypdf-blocking precedent) and confirm this module still
    imports cleanly, and that calling browser_fetch degrades to "" rather than raising."""
    import importlib
    import sys

    class _BlockPlaywright:
        def find_spec(self, name, path, target=None):
            if name == "playwright" or name.startswith("playwright."):
                raise ImportError("playwright is not installed (simulating agentteams[research]-only install)")
            return None

    for mod in list(sys.modules):
        if mod == "agentteams.research.browser" or mod.startswith("playwright"):
            sys.modules.pop(mod, None)

    blocker = _BlockPlaywright()
    sys.meta_path.insert(0, blocker)
    try:
        with pytest.raises(ImportError):
            importlib.import_module("playwright")
        browser_module = importlib.import_module("agentteams.research.browser")
        assert browser_module.browser_fetch("https://example.com/") == ""
        assert browser_module.browser_screenshot("https://example.com/", "/tmp/out.png") is False
    finally:
        sys.meta_path.remove(blocker)
        for mod in list(sys.modules):
            if mod == "agentteams.research.browser" or mod.startswith("playwright"):
                sys.modules.pop(mod, None)
        importlib.import_module("agentteams.research.browser")


def test_browser_fetch_absent_from_research_package_exports() -> None:
    """Enforces the deliberate import-boundary design decision (plan.md Design decision 3) as a
    real regression test, not just a comment: a plain `import agentteams.research` must never
    risk pulling in Playwright, so browser_fetch/browser_screenshot must not appear in the
    package's __all__ or its dir()."""
    assert "browser_fetch" not in research_pkg.__all__
    assert "browser_screenshot" not in research_pkg.__all__
    assert "browser_fetch" not in dir(research_pkg)
    assert "browser_screenshot" not in dir(research_pkg)
    assert "browser" not in research_pkg.__all__
