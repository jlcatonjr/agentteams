"""No-key web search and page-text fetching.

Search runs through a **chain of backends** (:mod:`agentteams.research.backends`) rather than a
single endpoint, and results are served from a **TTL disk cache**
(:mod:`agentteams.research.cache`) when one is warm. Only the caller-supplied query/URL is ever
sent. Any failure returns an empty result so a caller can degrade gracefully rather than crash.

The zero-configuration path remains fully functional: with no environment variables set, the
chain is ``duckduckgo`` → ``ddg_lite``, both key-free. Keyed/hosted backends are additional
links, never prerequisites.

Ported from LingoFriend (``knowledge/search.py``, commit-adjacent to 2026-07-19's PDF
content-type fix) — the origin of the ``max_pdf_bytes``/``pdf_timeout_s`` split and the lazy
``pypdf`` import documented below.
"""

from __future__ import annotations

import html as _html
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

# Imported from the SUBMODULE path, not `from agentteams.research import cache`: the latter
# creates a load-time edge back to the package `__init__`, which itself imports this module —
# a genuine import cycle that `tests/test_living_doc_and_cycles.py` rejects.
from agentteams.research.backends import CHALLENGE_STATUS as _DDG_CHALLENGE_STATUS_VALUE
from agentteams.research.backends import available_backends
from agentteams.research.cache import load as _cache_load
from agentteams.research.cache import make_key as _cache_key
from agentteams.research.cache import store as _cache_store

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"

_JSON_LD_BLOCK = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_JSON_LD_DATE = re.compile(r'"date(?:Published|Created)"\s*:\s*"([^"]+)"')
_META_ARTICLE_TIME = re.compile(
    r'<meta[^>]*property=["\']article:published_time["\'][^>]*content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_META_DATE = re.compile(
    r'<meta[^>]*name=["\'](?:date|pubdate|publish-date)["\'][^>]*content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_TIME_TAG = re.compile(r'<time[^>]*datetime=["\']([^"\']+)["\']', re.IGNORECASE)


@dataclass
class Source:
    title: str
    url: str
    snippet: str


#: DuckDuckGo answers a challenged request with 202 + an interstitial page rather than an
#: error status, so ``raise_for_status()`` never fires (202 IS success). Observed 2026-07-24:
#: long, highly specific queries are challenged *deterministically* (4/4 attempts -> 202, zero
#: parseable results) while a short form of the same query returns 200 with 10 results. Without
#: this discriminator the caller cannot tell "blocked" from "nothing matched" -- an agent reads
#: the empty list as "no such information exists" and stops searching.
#:
#: Re-exported from :mod:`agentteams.research.backends`, which now owns per-backend response
#: parsing; kept as a module attribute here because it is part of this module's documented
#: vocabulary.
_DDG_CHALLENGE_STATUS = _DDG_CHALLENGE_STATUS_VALUE

#: Broadening keeps the first half of the query, never fewer than this many terms.
#: A fixed small cap was tried first and rejected: truncating the motivating query
#: ("2026 NASCAR Cup Pennzoil 400 Las Vegas top 10 finishers results") to 4 terms
#: yields "2026 NASCAR Cup Pennzoil" — which severs the race name "Pennzoil 400",
#: the very entity the search needs. Halving yields "2026 NASCAR Cup Pennzoil 400",
#: keeping it intact. Both were verified to return 200 with 10 results, so the
#: choice is about preserving the entity, not about getting past the challenge.
_MIN_BROADEN_TERMS = 3


def _broaden(query: str) -> str:
    """Return a shorter form of ``query`` for a retry, or "" when it can't be shortened.

    Keeps the leading half of the terms — long queries put the specific entity first
    and descriptive filler last, and it is the filler that draws the challenge. Halving
    (rather than truncating to a fixed length) scales with the query, so a very long
    query is cut hard while a mildly long one loses only its tail.

    Args:
        query: The query that was challenged.

    Returns:
        The broadened query, or ``""`` when ``query`` is already at/below the floor
        and shortening it further would strip meaning rather than filler.
    """
    terms = query.split()
    if len(terms) <= _MIN_BROADEN_TERMS:
        return ""
    kept = max(_MIN_BROADEN_TERMS, len(terms) // 2)
    if kept >= len(terms):
        return ""
    return " ".join(terms[:kept])


@dataclass
class SearchProvenance:
    """Where a result set actually came from — for the external-retrieval quality gate.

    A summary resting on retrieved information has to be able to say which backend answered,
    whether the answer was live or cached, and whether the query was altered on the way. Before
    this existed a caller could report a URL but not how it was obtained.
    """

    backend: str | None
    cached: bool
    query_used: str
    backends_tried: tuple[str, ...]
    challenged: bool


def _try_chain(query: str, k: int, timeout_s: float) -> tuple[list[Source], bool, str | None, list[str]]:
    """Run ``query`` against each available backend in order, stopping at the first that answers.

    Args:
        query: The exact query string to issue.
        k: Maximum results.
        timeout_s: Per-request timeout.

    Returns:
        ``(results, any_challenged, answering_backend, backends_tried)``. ``any_challenged`` is
        True when at least one backend actively deflected the request and none answered — the
        signal that broadening is worth trying. It is deliberately distinct from "every backend
        returned an honest zero", which must NOT trigger broadening.
    """
    tried: list[str] = []
    any_challenged = False
    for backend in available_backends():
        hits, challenged = backend.search(query, k, timeout_s)
        tried.append(backend.name)
        any_challenged = any_challenged or challenged
        if hits:
            return (
                [Source(title=h.title, url=h.url, snippet=h.snippet) for h in hits],
                False,
                backend.name,
                tried,
            )
    return [], any_challenged, None, tried


def web_search(query: str, k: int = 5, timeout_s: float = 8.0) -> list[Source]:
    """Return up to ``k`` search results for ``query`` (title, resolved url, snippet).

    When the upstream challenges the request (see ``_DDG_CHALLENGE_STATUS``) rather than
    answering it, retries **once** with a broadened query instead of reporting an empty
    result set. A challenge is not evidence that nothing matched, and treating it as such
    is what makes a caller give up on a question that is in fact answerable.

    Args:
        query: Free-text search query.
        k: Maximum number of results to return.
        timeout_s: Per-request timeout in seconds.

    Returns:
        Up to ``k`` results; empty when the query is blank, the network fails, or nothing
        matched. Use :func:`web_search_verbose` when the caller needs to distinguish those.
    """
    return web_search_verbose(query, k=k, timeout_s=timeout_s)[0]


def _broadening_forms(query: str) -> list[str]:
    """Return successively broader forms of ``query``, most specific first.

    ``_broaden`` halves once. Measured 2026-07-30, one halving is not always enough: a
    12-term query challenged at full length was still challenged at 6 terms. Applying it
    repeatedly walks down to the floor instead of giving up after a single step.

    Args:
        query: The original query.

    Returns:
        Broadened forms excluding the original, ordered most-specific to least. Empty when
        ``query`` is already at or below the broadening floor.
    """
    forms: list[str] = []
    current = query
    while True:
        nxt = _broaden(current)
        if not nxt or nxt in forms:
            break
        forms.append(nxt)
        current = nxt
    return forms


def web_search_verbose(
    query: str, k: int = 5, timeout_s: float = 8.0,
) -> tuple[list[Source], str | None]:
    """:func:`web_search` plus a note describing any fallback or block.

    Tries every available backend on the original query before altering the query at all —
    switching provider loses nothing, whereas broadening discards search terms. Only when the
    whole chain has been *challenged* (not merely empty) does it broaden and try the chain
    again, progressively, down to the broadening floor.

    Args:
        query: Free-text search query.
        k: Maximum number of results to return.
        timeout_s: Per-request timeout in seconds.

    Returns:
        ``(results, note)``. ``note`` is ``None`` on an ordinary search; otherwise a short
        human-readable explanation — that the query was broadened, that a non-default backend
        answered, or that the upstream challenged the request and the caller should not read
        the empty list as "no such information exists."
    """
    results, note, _ = web_search_with_provenance(query, k=k, timeout_s=timeout_s)
    return results, note


def web_search_with_provenance(
    query: str, k: int = 5, timeout_s: float = 8.0,
) -> tuple[list[Source], str | None, SearchProvenance]:
    """:func:`web_search_verbose` plus a structured :class:`SearchProvenance` record.

    The external-retrieval quality gate requires a summary to state how its evidence was
    obtained; ``note`` is prose for a human, this is the machine-readable form.

    Args:
        query: Free-text search query.
        k: Maximum number of results to return.
        timeout_s: Per-request timeout in seconds.

    Returns:
        ``(results, note, provenance)``.
    """
    empty = SearchProvenance(
        backend=None, cached=False, query_used=query, backends_tried=(), challenged=False
    )
    if not query.strip():
        return [], None, empty

    chain = tuple(b.name for b in available_backends())
    cache_key = _cache_key("search", query, k, *chain)
    cached = _cache_load(cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("results"), list):
        hits = [
            Source(title=str(r.get("title", "")), url=str(r.get("url", "")),
                   snippet=str(r.get("snippet", "")))
            for r in cached["results"]
            if isinstance(r, dict)
        ]
        return hits, cached.get("note"), SearchProvenance(
            backend=cached.get("backend"),
            cached=True,
            query_used=str(cached.get("query_used", query)),
            backends_tried=tuple(cached.get("backends_tried", ())),
            challenged=False,
        )

    def _finish(
        results: list[Source], note: str | None, backend: str | None,
        query_used: str, tried: list[str], challenged: bool,
    ) -> tuple[list[Source], str | None, SearchProvenance]:
        prov = SearchProvenance(
            backend=backend, cached=False, query_used=query_used,
            backends_tried=tuple(tried), challenged=challenged,
        )
        if results:
            _cache_store(cache_key, {
                "results": [r.__dict__ for r in results],
                "note": note,
                "backend": backend,
                "query_used": query_used,
                "backends_tried": list(tried),
            })
        return results, note, prov

    results, challenged, backend, tried = _try_chain(query, k, timeout_s)
    if results:
        # A non-first backend answering is worth saying out loud — it tells an operator the
        # primary endpoint is degraded — but it is not a caveat about the RESULTS, so the note
        # stays None for the ordinary case of the first backend answering.
        note = None if tried[:1] == [backend] else f"answered by the {backend!r} backend"
        return _finish(results, note, backend, query, tried, False)
    if not challenged:
        # Every backend returned an honest zero. Broadening a query nothing matched only
        # produces less precise nothing.
        return _finish([], None, None, query, tried, False)

    all_tried = list(tried)
    forms = _broadening_forms(query)
    if not forms:
        return _finish(
            [], "search endpoint challenged this request (no results returned); "
                "this is not evidence that nothing matched — retry shortly",
            None, query, all_tried, True,
        )

    for form in forms:
        retried, still_challenged, backend, tried = _try_chain(form, k, timeout_s)
        all_tried.extend(tried)
        if retried:
            return _finish(
                retried,
                f"original query was challenged by the search endpoint; "
                f"retried with the broader query {form!r}",
                backend, form, all_tried, False,
            )
        if not still_challenged:
            return _finish([], f"no results for {query!r} or the broader {form!r}",
                           None, form, all_tried, False)
    return _finish(
        [], f"search endpoint challenged {query!r} and every broader form tried "
            f"({', '.join(repr(f) for f in forms)}); not evidence that nothing matched "
            f"— retry shortly",
        None, forms[-1], all_tried, True,
    )


def is_public_https(url: str) -> bool:
    """SSRF guard: https only, and the host must not resolve to a private/loopback IP.

    Public (not underscore-prefixed) because ``agentteams.research.browser`` reuses this exact
    check as its own pre-navigation gate before launching a browser — deliberately the one
    cross-submodule import of a previously-module-private name in this package (every other
    cross-module import here, e.g. ``reputable.py``'s ``from agentteams.research.search import
    Source, web_search``, only ever touched already-public names). A browser context additionally
    needs a *second*, per-request guard of its own (redirects and page-initiated JS requests never
    pass back through this function) — see ``browser.py``'s ``page.route`` handler; this function
    is necessary but not sufficient there.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        addr = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
        return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved)
    except (socket.gaierror, ValueError, UnicodeError):
        # CH-24: named types — DNS resolution failure, malformed IP-literal parsing, or a
        # non-ASCII hostname encoding failure. A fail-closed SSRF guard: any of these means the
        # URL cannot be confirmed safe, so treat it as not public/https.
        return False


def _extract_pdf_text(body: bytes) -> str:
    """Extract text from a PDF byte string. Empty on any failure — never raises.

    ``import pypdf`` is deliberately LAZY (inside this function, not at module level): this
    module is meant to be importable by lightweight callers that never touch a PDF, and ``pypdf``
    is only in the ``research`` optional-dependency group, not this package's base install. A
    module-level ``import pypdf`` would make it a hard dependency of every caller. Wrapped in a
    broad ``except Exception`` (covers ``pypdf.errors.PdfReadError``/``PdfStreamError`` and any
    other malformed-input exception) to match this module's degrade-don't-raise contract.
    """
    try:
        import io

        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(body))
        pages = [page.extract_text() or "" for page in reader.pages]
        return re.sub(r"\s+", " ", " ".join(pages)).strip()
    except Exception:  # noqa: BLE001 — CH-24: third-party parser on adversarial external bytes.
        # A malformed/corrupted PDF can make pypdf raise far beyond its own documented
        # PdfReadError/PdfStreamError (real-world corrupt files have triggered raw ValueError/
        # KeyError/IndexError/zlib.error deep in pypdf's own parsing internals) — the exception
        # surface for arbitrary untrusted external input is not enumerable in advance, which is
        # exactly the "genuinely unavoidable external failure" boundary CH-24 reserves broad
        # except for.
        return ""


def extract_published_date(html: str) -> str | None:
    """Best-effort publish-date extraction from a page's raw (unstripped) HTML.

    Tried, in order: JSON-LD ``datePublished``/``dateCreated`` inside an ``application/ld+json``
    script block; an ``article:published_time`` meta tag; a ``date``/``pubdate``/``publish-date``
    meta tag; a bare ``<time datetime="...">`` tag. Returns the raw string as found — never
    normalizes, guesses, or fabricates a date. Returns ``None`` on no match. Never raises: a page
    with no extractable date is an honest empty, not a caller-visible error.

    Must be called against the RAW html — ``fetch_text``'s own stripping regex removes ``<script>``
    blocks (where JSON-LD dates live) before returning, so this only sees what it needs when run
    before that stripping, not after (see ``fetch_text_and_date``).
    """
    for block in _JSON_LD_BLOCK.findall(html):
        match = _JSON_LD_DATE.search(block)
        if match:
            return match.group(1)
    for pattern in (_META_ARTICLE_TIME, _META_DATE, _TIME_TAG):
        match = pattern.search(html)
        if match:
            return match.group(1)
    return None


#: HTML download cap. Was 40_000, which silently truncated any real article to its
#: <head> + navigation chrome: measured 2026-07-24, an en.wikipedia.org article
#: yielded 342 chars and ZERO body content at 40 KB versus 17,744 chars containing
#: the full data table at 400 KB. The failure was invisible -- text returned, no
#: error -- so a caller could not tell "page has no such content" from "we never
#: downloaded the part that has it". This bounds the DOWNLOAD; `max_chars` separately
#: bounds what reaches the caller, so raising this does not enlarge anyone's context.
_DEFAULT_MAX_BYTES = 400_000


def _fetch_raw(
    url: str, max_bytes: int, timeout_s: float, max_pdf_bytes: int, pdf_timeout_s: float
) -> tuple[bytes, str, str] | None:
    """Shared fetch core for ``fetch_text``/``fetch_text_and_date`` — one network round-trip,
    two possible post-processing paths. Returns ``(body, content_type, encoding)``, or ``None`` on
    any guard/failure/non-200 (both callers convert that into their own empty-result shape).
    """
    if not is_public_https(url):
        return None
    try:
        deadline = time.monotonic() + timeout_s
        with httpx.stream(
            "GET", url, headers={"User-Agent": _UA}, timeout=timeout_s, follow_redirects=False
        ) as resp:
            if resp.status_code != 200:
                return None
            content_type = resp.headers.get("content-type", "")
            is_pdf = "application/pdf" in content_type
            cap = max_pdf_bytes if is_pdf else max_bytes
            if is_pdf:
                # A separate, later-computed deadline (not known until the header is read) — PDFs
                # get pdf_timeout_s's larger wall-clock budget instead of timeout_s's.
                deadline = time.monotonic() + pdf_timeout_s
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= cap or time.monotonic() >= deadline:
                    break
            body = b"".join(chunks)
            encoding = resp.encoding or "utf-8"
    except httpx.HTTPError:
        # CH-24: named type — see web_search()'s identical rationale; this streams the same
        # httpx request/response boundary (connect/timeout/transport failures mid-stream).
        return None
    return body, content_type, encoding


def strip_html_to_text(html: str, max_chars: int) -> str:
    """Shared HTML→text extraction: drop script/style/nav/footer/header blocks, strip remaining
    tags, unescape entities, collapse whitespace, cap at ``max_chars``.

    Public (not module-private) because ``agentteams.research.browser`` reuses this exact
    extraction — applied there to a browser's rendered ``page.content()`` instead of a raw HTTP
    response body — so ``fetch_text`` and ``browser_fetch`` return text in a consistent shape.
    Hoisted here after CH-24's sibling CH-08 duplication guard was crossed (the same 3-statement
    sequence had drifted into three call sites — this function, ``fetch_text_and_date``, and
    ``browser.py`` — see ``tmp/by-week/2026-W30/web-browsing-playwright-cli.plan.md``).
    """
    text = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


def fetch_text(
    url: str,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    timeout_s: float = 8.0,
    max_chars: int = 4000,
    max_pdf_bytes: int = 12_000_000,
    pdf_timeout_s: float = 60.0,
) -> str:
    """Fetch a page body and return extracted text (bounded). Empty on any failure/guard.

    Public-https only, no redirects to private hosts, size-capped. ``max_chars`` bounds the
    returned text.

    Content-type aware: a PDF response (detected via the ``Content-Type`` header, checked BEFORE
    the byte-read loop, OR — for a response that omits/mislabels it — the ``%PDF-`` magic-number
    prefix on whatever was already read under the HTML-sized cap) is routed to
    ``_extract_pdf_text`` instead of being HTML-tag-stripped.

    ``max_pdf_bytes``/``pdf_timeout_s`` are deliberately SEPARATE, larger budgets than
    ``max_bytes``/``timeout_s``: unlike HTML, a PDF cannot be parsed from an arbitrary byte
    truncation (its cross-reference table/trailer lives at the end of the file), and a real PDF
    can take far longer to transfer than typical HTML front-matter. ``timeout_s`` still governs
    httpx's own per-chunk read-gap timeout unchanged; the wall-clock deadline below is a separate,
    independent bound — a server trickling data steadily (each chunk arriving well within the
    per-chunk window) never trips httpx's own timeout even when the full transfer takes minutes,
    so only the wall-clock side needs widening for PDFs.
    """
    raw = _fetch_raw(url, max_bytes, timeout_s, max_pdf_bytes, pdf_timeout_s)
    if raw is None:
        return ""
    body, content_type, encoding = raw
    if "application/pdf" in content_type or body.startswith(b"%PDF-"):
        return _extract_pdf_text(body)[:max_chars]
    return strip_html_to_text(body.decode(encoding, errors="ignore"), max_chars)


def fetch_text_and_date(
    url: str,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    timeout_s: float = 8.0,
    max_chars: int = 4000,
    max_pdf_bytes: int = 12_000_000,
    pdf_timeout_s: float = 60.0,
) -> tuple[str, str | None]:
    """Fetch a page once and return both its extracted text (identical to what ``fetch_text``
    would return for the same input) and a best-effort publish date — for a caller who wants both
    without paying for two fetches. Additive: does not change ``fetch_text``'s own signature or
    behavior.

    A PDF response never carries an extractable date via this module's regex-based approach
    (dates live in HTML meta/JSON-LD, not PDF structure) — returns ``(text, None)`` for a PDF, the
    same honest-empty shape as any other page with no extractable date.
    """
    raw = _fetch_raw(url, max_bytes, timeout_s, max_pdf_bytes, pdf_timeout_s)
    if raw is None:
        return "", None
    body, content_type, encoding = raw
    if "application/pdf" in content_type or body.startswith(b"%PDF-"):
        return _extract_pdf_text(body)[:max_chars], None
    raw_text = body.decode(encoding, errors="ignore")
    published_at = extract_published_date(raw_text)
    return strip_html_to_text(raw_text, max_chars), published_at
