"""Pluggable web-search backends — the fallback chain behind ``search.web_search``.

Before this module there was exactly one backend (DuckDuckGo's HTML endpoint) and no
fallback. Measured 2026-07-30, long multi-concept queries — precisely the shape a research
agent produces — are challenged deterministically, and ``search._broaden``'s single halving
step does not clear it: the query
``"retrieval augmented generation 2026 best practices for local code search"`` returned zero
results on both its original and its broadened form. An agent reads that empty list as "no
such information exists" and abandons an answerable question.

Design constraints this module is bound by:

- **The zero-configuration path must stay fully functional.** A base
  ``pip install agentteams[research]`` with no environment variables set gets a real chain
  (``duckduckgo`` → ``ddg_lite``), not a single point of failure. Backends needing a key or a
  host are *additional links*, never prerequisites. A remediation whose benefit requires opt-in
  does not remediate the reported defect.
- **CLI-mediated, never MCP.** These backends are plain HTTPS calls made by this package and
  surfaced through ``python -m agentteams.research search``. See
  ``references/retrieval-transport-policy.md`` for why that is a standing constraint and not
  an implementation accident.
- **Degrade, never raise.** Same contract as the rest of this package: any backend failure
  returns an empty result and lets the chain continue.

Adding a backend: implement :class:`SearchBackend`, append it to :data:`_BACKEND_ORDER`. A
backend whose ``available()`` returns False is skipped silently and costs nothing.
"""

from __future__ import annotations

import html as _html
import json
import os
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs, unquote, urlparse

import httpx

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"

#: DuckDuckGo answers a challenged request with 202 + an interstitial page rather than an error
#: status, so ``raise_for_status()`` never fires (202 IS success). Without this discriminator a
#: caller cannot tell "blocked" from "nothing matched".
CHALLENGE_STATUS = 202

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"

_RESULT_A = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)

#: lite.duckduckgo.com serves a table layout with no ``result__a`` class — a different endpoint
#: with a different renderer, which is exactly why it is worth having as a fallback: it is
#: challenged independently of the HTML endpoint rather than in lockstep with it.
_LITE_ROW = re.compile(r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_LITE_SNIPPET = re.compile(r'class="result-snippet"[^>]*>(.*?)</td>', re.DOTALL)


@dataclass
class RawHit:
    """One backend-agnostic search hit, before it is adapted to ``search.Source``."""

    title: str
    url: str
    snippet: str


def _strip(text: str) -> str:
    """Strip HTML tags and unescape entities from ``text``.

    Args:
        text: A fragment of HTML.

    Returns:
        The plain-text content, whitespace-trimmed.
    """
    return _html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def resolve_redirect(href: str) -> str:
    """Unwrap a DuckDuckGo redirect wrapper to the underlying target URL.

    DDG returns results as ``//duckduckgo.com/l/?uddg=<encoded>``. Both DDG backends share
    this, so it lives here rather than being duplicated per backend.

    Args:
        href: The href as it appeared in the result page.

    Returns:
        The unwrapped target URL, or ``href`` unchanged when it is not a wrapper or cannot
        be parsed.
    """
    if "uddg=" not in href:
        return href
    try:
        query = urlparse(href if href.startswith("http") else "https:" + href).query
        target = parse_qs(query).get("uddg", [href])[0]
        return unquote(target)
    except (ValueError, IndexError, UnicodeError):  # CH-24: named types, not blanket
        return href


@runtime_checkable
class SearchBackend(Protocol):
    """One search provider in the fallback chain."""

    name: str

    def available(self) -> bool:
        """Whether this backend is usable in the current environment.

        Returns:
            True when the backend can be called — for keyed/hosted backends this means the
            required environment variable is set. A False here skips the backend silently.
        """
        ...

    def search(self, query: str, k: int, timeout_s: float) -> tuple[list[RawHit], bool]:
        """Run one search.

        Args:
            query: Free-text search query.
            k: Maximum number of hits to return.
            timeout_s: Per-request timeout in seconds.

        Returns:
            ``(hits, was_challenged)``. ``was_challenged`` is True only when the provider
            actively refused/deflected the request — which is NOT the same as "nothing
            matched", and is what tells the chain to try the next backend.
        """
        ...


class _DuckDuckGoHTMLBackend:
    """DuckDuckGo's ``html.duckduckgo.com`` endpoint. Key-free; the historical default."""

    name = "duckduckgo"

    def available(self) -> bool:
        """Always available — no key, no configuration.

        Returns:
            True.
        """
        return True

    def search(self, query: str, k: int, timeout_s: float) -> tuple[list[RawHit], bool]:
        """Search via the DDG HTML endpoint. See :meth:`SearchBackend.search`."""
        try:
            resp = httpx.get(
                _DDG_HTML_URL,
                params={"q": query},
                headers={"User-Agent": _UA},
                timeout=timeout_s,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            # CH-24: named type — httpx.HTTPError is the base for every exception httpx itself
            # raises (connect/timeout/transport, and raise_for_status's HTTPStatusError). A
            # genuinely unavoidable network-I/O boundary, not a blanket catch-all.
            return [], False
        titles = _RESULT_A.findall(resp.text)
        snippets = [_strip(s) for s in _SNIPPET.findall(resp.text)]
        hits = [
            RawHit(
                title=_strip(title),
                url=resolve_redirect(href),
                snippet=snippets[i] if i < len(snippets) else "",
            )
            for i, (href, title) in enumerate(titles[:k])
        ]
        return hits, (not hits and resp.status_code == CHALLENGE_STATUS)


class _DuckDuckGoLiteBackend:
    """DuckDuckGo's ``lite.duckduckgo.com`` endpoint — key-free, independently challenged.

    Deliberately zero-configuration: this is the link that makes the chain useful to an
    operator who has set no environment variables at all, which is the default case.
    """

    name = "ddg_lite"

    def available(self) -> bool:
        """Always available — no key, no configuration.

        Returns:
            True.
        """
        return True

    def search(self, query: str, k: int, timeout_s: float) -> tuple[list[RawHit], bool]:
        """Search via the DDG lite endpoint. See :meth:`SearchBackend.search`."""
        try:
            resp = httpx.get(
                _DDG_LITE_URL,
                params={"q": query},
                headers={"User-Agent": _UA},
                timeout=timeout_s,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError:  # CH-24: named type — see the HTML backend's rationale.
            return [], False
        rows = _LITE_ROW.findall(resp.text)
        snippets = [_strip(s) for s in _LITE_SNIPPET.findall(resp.text)]
        hits = [
            RawHit(
                title=_strip(title),
                url=resolve_redirect(href),
                snippet=snippets[i] if i < len(snippets) else "",
            )
            for i, (href, title) in enumerate(rows[:k])
        ]
        return hits, (not hits and resp.status_code == CHALLENGE_STATUS)


#: Environment variable naming a SearXNG instance base URL (e.g. ``https://searx.example.org``).
#: Unset by default: this package ships no default third-party instance, because silently routing
#: an operator's queries through someone else's host is not a decision a library gets to make.
SEARXNG_URL_ENV = "AGENTTEAMS_SEARXNG_URL"


class _SearxngBackend:
    """A self-hosted or operator-chosen SearXNG instance. Enabled only via env var."""

    name = "searxng"

    def available(self) -> bool:
        """Whether a SearXNG base URL has been configured.

        Returns:
            True when :data:`SEARXNG_URL_ENV` is set to a non-empty value.
        """
        return bool(os.environ.get(SEARXNG_URL_ENV, "").strip())

    def search(self, query: str, k: int, timeout_s: float) -> tuple[list[RawHit], bool]:
        """Search via SearXNG's JSON API. See :meth:`SearchBackend.search`.

        Many public instances disable the JSON format; a non-JSON response degrades to an
        empty, non-challenged result so the chain simply moves on.
        """
        base = os.environ.get(SEARXNG_URL_ENV, "").strip().rstrip("/")
        if not base:
            return [], False
        try:
            resp = httpx.get(
                f"{base}/search",
                params={"q": query, "format": "json"},
                headers={"User-Agent": _UA},
                timeout=timeout_s,
                follow_redirects=True,
            )
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            # CH-24: named types — transport failure, or an instance that answered with HTML
            # because it has the JSON format disabled.
            return [], False
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return [], False
        hits = [
            RawHit(
                title=str(item.get("title", "")).strip(),
                url=str(item.get("url", "")).strip(),
                snippet=str(item.get("content", "")).strip(),
            )
            for item in results[:k]
            if isinstance(item, dict) and item.get("url")
        ]
        return hits, False


#: Environment variable holding a Brave Search API subscription token. Unset by default —
#: this package never ships or requires a key.
BRAVE_KEY_ENV = "AGENTTEAMS_BRAVE_API_KEY"

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


class _BraveBackend:
    """Brave Search API. Enabled only when a key is present in the environment."""

    name = "brave"

    def available(self) -> bool:
        """Whether a Brave API key has been configured.

        Returns:
            True when :data:`BRAVE_KEY_ENV` is set to a non-empty value.
        """
        return bool(os.environ.get(BRAVE_KEY_ENV, "").strip())

    def search(self, query: str, k: int, timeout_s: float) -> tuple[list[RawHit], bool]:
        """Search via the Brave Search API. See :meth:`SearchBackend.search`."""
        key = os.environ.get(BRAVE_KEY_ENV, "").strip()
        if not key:
            return [], False
        try:
            resp = httpx.get(
                _BRAVE_URL,
                params={"q": query, "count": k},
                headers={
                    "User-Agent": _UA,
                    "Accept": "application/json",
                    "X-Subscription-Token": key,
                },
                timeout=timeout_s,
            )
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            # CH-24: named types — transport failure or a non-JSON error body.
            return [], False
        web = payload.get("web") if isinstance(payload, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        if not isinstance(results, list):
            return [], False
        hits = [
            RawHit(
                title=str(item.get("title", "")).strip(),
                url=str(item.get("url", "")).strip(),
                snippet=str(item.get("description", "")).strip(),
            )
            for item in results[:k]
            if isinstance(item, dict) and item.get("url")
        ]
        return hits, False


#: Chain order. The two key-free DuckDuckGo endpoints come first so the default install has a
#: real fallback; operator-configured backends follow. Order is deliberate, not incidental:
#: an operator who configures Brave still gets the free endpoints tried first.
_BACKEND_ORDER: tuple[SearchBackend, ...] = (
    _DuckDuckGoHTMLBackend(),
    _DuckDuckGoLiteBackend(),
    _SearxngBackend(),
    _BraveBackend(),
)


def available_backends() -> list[SearchBackend]:
    """Return the backends usable right now, in chain order.

    Returns:
        Every backend whose ``available()`` is True. Always non-empty in practice — the two
        DuckDuckGo backends require no configuration.
    """
    return [b for b in _BACKEND_ORDER if b.available()]


def backend_names() -> list[str]:
    """Return the names of currently-available backends, in chain order.

    Returns:
        Backend name strings — useful for the provenance note a caller surfaces to an agent.
    """
    return [b.name for b in available_backends()]
