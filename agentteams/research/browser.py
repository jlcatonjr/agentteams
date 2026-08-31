"""Playwright-backed browser fetch/screenshot — for pages ``agentteams.research.search.fetch_text``
can't reach because they need JavaScript to render.

Deliberately NOT imported by ``agentteams/research/__init__.py`` (unlike ``search``/``reputable``/
``verify``/``news``, which are eagerly re-exported there): a plain ``import agentteams.research``
must never risk importing Playwright, which — unlike this package's other optional dependencies —
needs a separate, heavy browser-binary install (``playwright install chromium``) beyond a plain
``pip install``. Reach this module explicitly: ``from agentteams.research.browser import
browser_fetch`` (requires the ``agentteams[browser]`` extra), or
``python -m agentteams.research browser <url>``.

Same degrade-don't-raise contract as the rest of this package: every function returns an honest
empty result (``""``/``False``) on any failure — missing ``playwright`` package, missing browser
binaries, navigation timeout, blocked request — never raises.

SSRF posture (two layers, not one — see ``tmp/by-week/2026-W30/web-browsing-playwright-cli.plan.md``
Design decision 1 for the full audit trail this resulted from): the initial URL is checked with
``agentteams.research.search.is_public_https`` before a browser is even launched, AND every
subsequent request the live page attempts (redirects, subresources, page-initiated JS
``fetch``/``XHR``) is re-checked by the same guard via a Playwright ``page.route`` handler — a
single pre-navigation check alone is insufficient for a real browser, which follows redirects and
runs arbitrary page JavaScript capable of issuing its own requests. NOT defended: DNS rebinding
(this guard's DNS resolution and Chromium's own subsequent connection are two separate
resolutions, not atomically one) — named honestly as a residual gap, not silently assumed away.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from agentteams.research.search import is_public_https, strip_html_to_text

# Applied to page.content() (a decoded str, unlike _fetch_raw's raw byte stream) immediately after
# retrieval, before any regex tag-stripping — a cap-after-materialization, not a true streaming
# cap: Playwright's page.content() is synchronous and always returns the full rendered DOM, so
# there is no earlier point to interrupt at the way _fetch_raw interrupts its byte-by-byte read.
_MAX_RAW_HTML_CHARS = 2_000_000


def _route_guard(route: Any) -> None:
    """Playwright route handler: abort any request whose URL fails ``is_public_https``.

    Wired via ``page.route("**/*", _route_guard)`` — Playwright invokes this for the initial
    navigation, every redirect hop, and every subresource/JS-initiated request a live page
    attempts. This closes the gap a single pre-navigation check leaves open. Takes any object
    exposing ``request.url`` and ``continue_()``/``abort()`` (Playwright's real ``Route`` at
    runtime; a lightweight stand-in in tests) — not type-pinned to ``playwright.sync_api.Route``
    so this function stays importable and directly unit-testable without Playwright installed.
    """
    if is_public_https(route.request.url):
        route.continue_()
    else:
        route.abort()


def _extract_text(rendered_html: str, max_chars: int) -> str:
    """Apply the raw-HTML size cap (see ``_MAX_RAW_HTML_CHARS`` above), then delegate to
    ``search.strip_html_to_text`` for the actual tag-strip/unescape/whitespace-collapse — kept
    as a shared function (not duplicated a third time here) so ``fetch_text`` and ``browser_fetch``
    return text in the same shape regardless of which one a caller used."""
    return strip_html_to_text(rendered_html[:_MAX_RAW_HTML_CHARS], max_chars)


@contextmanager
def _rendered_page(
    url: str, *, headed: bool, wait_until: str, timeout_s: float
) -> Iterator[Any]:
    """Launch Chromium, navigate to ``url`` with the per-request SSRF guard installed, yield the
    loaded ``Page``. The caller extracts what it needs (``content()``/``screenshot()``) inside the
    ``with`` block; the browser is always closed on exit.

    Raises on any failure (missing ``playwright`` package, missing browser binaries, navigation
    timeout, or any other Playwright-raised error) — the two public functions below translate that
    into their own honest-empty return at the boundary a caller actually sees. Kept as an
    exception-propagating internal helper rather than degrading here, so both public functions
    share one navigation/cleanup implementation instead of duplicating it.
    """
    from playwright.sync_api import sync_playwright  # lazy: see module docstring.

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        try:
            page = browser.new_page()
            page.route("**/*", _route_guard)
            page.goto(url, wait_until=wait_until, timeout=timeout_s * 1000)
            yield page
        finally:
            browser.close()


def _safe_render(
    url: str, *, headed: bool, wait_until: str, timeout_s: float, extract: Any
) -> Any:
    """Run ``extract(page)`` against a rendered page inside the ONE broad-except boundary this
    module needs. Both public functions below route through here so this module contributes a
    single new broad catch to this project's CH-24 ratchet (``BROAD_EXCEPT_BASELINE`` in
    ``tests/test_code_hygiene.py``), not two.

    Returns ``extract(page)``'s result on success, ``None`` on ANY failure — missing
    ``playwright`` package, missing browser binaries, navigation timeout, protocol error, page
    crash. The exception surface of a third-party browser automation library driving an
    arbitrary, untrusted external page is not enumerable in advance — the same category of
    boundary this package's own ``search.py._extract_pdf_text`` already established a precedent
    for (a third-party parser over adversarial external bytes).
    """
    try:
        with _rendered_page(
            url, headed=headed, wait_until=wait_until, timeout_s=timeout_s
        ) as page:
            return extract(page)
    except (ImportError, ModuleNotFoundError):
        return None
    except Exception:  # noqa: BLE001 — CH-24: see this function's own docstring.
        return None


def browser_fetch(
    url: str,
    *,
    headed: bool = False,
    wait_until: str = "networkidle",
    timeout_s: float = 20.0,
    max_chars: int = 4000,
) -> str:
    """Render ``url`` in a real (Chromium) browser and return extracted, bounded text.

    Use this only when ``agentteams.research.search.fetch_text`` isn't enough — i.e. the page
    needs JavaScript to populate its content. Slower and heavier than a plain fetch; try
    ``fetch_text`` first.

    ``headed=False`` by default: this is a one-shot CLI/programmatic call from an agent's shell
    tool, most often on a server/container/CI runner with no display attached — a headed launch
    there would simply fail. ``headed=True`` is for a human operator co-located with a real
    display who wants to watch (debugging, demos, manual login/2FA assistance) — it changes
    nothing about what THIS function returns; the agent itself has no way to perceive a rendered
    window either way, headed or not.

    ``wait_until``: one of Playwright's navigation wait conditions (``"load"``,
    ``"domcontentloaded"``, ``"networkidle"``). Defaults to ``"networkidle"`` — a better fit than
    ``"load"`` for JS-hydration-heavy pages (the class this function exists for), which often fire
    the ``load`` event before client-side rendering has populated real content. Known tradeoff:
    ``"networkidle"`` never fires on a page with continuous background activity (long-polling,
    websockets) — pass ``wait_until="load"`` or ``"domcontentloaded"`` for those.

    Returns ``""`` on any failure: ``playwright`` not installed, browser binaries not installed
    (``playwright install chromium`` not yet run), the initial URL failing the SSRF guard,
    navigation timeout, or any other Playwright-raised error. Never raises.
    """
    if not is_public_https(url):
        return ""
    content = _safe_render(
        url,
        headed=headed,
        wait_until=wait_until,
        timeout_s=timeout_s,
        extract=lambda page: page.content(),
    )
    if content is None:
        return ""
    return _extract_text(content, max_chars)


def browser_screenshot(
    url: str,
    output_path: str,
    *,
    headed: bool = False,
    wait_until: str = "networkidle",
    timeout_s: float = 20.0,
) -> bool:
    """Render ``url`` and save a full-page screenshot to ``output_path``.

    Returns ``True`` on success, ``False`` on any failure — same failure modes and never-raises
    contract as ``browser_fetch``; see its docstring for ``headed``/``wait_until`` semantics.
    """
    if not is_public_https(url):
        return False

    def _take_screenshot(page: Any) -> bool:
        page.screenshot(path=output_path, full_page=True)
        return True

    return (
        _safe_render(
            url,
            headed=headed,
            wait_until=wait_until,
            timeout_s=timeout_s,
            extract=_take_screenshot,
        )
        is True
    )
