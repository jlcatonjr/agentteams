"""No-key web search and page-text fetching.

Uses DuckDuckGo's HTML endpoint over plain ``httpx`` (no API key, no browser automation). Only
the caller-supplied query/URL is ever sent. Any failure returns an empty result so a caller can
degrade gracefully rather than crash.

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
from urllib.parse import parse_qs, unquote, urlparse

import httpx

_DDG_URL = "https://html.duckduckgo.com/html/"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
_RESULT_A = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)


@dataclass
class Source:
    title: str
    url: str
    snippet: str


def _strip(text: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _resolve_url(href: str) -> str:
    """DDG wraps results as ``//duckduckgo.com/l/?uddg=<encoded>`` — unwrap to the real URL."""
    if "uddg=" in href:
        try:
            query = urlparse(href if href.startswith("http") else "https:" + href).query
            target = parse_qs(query).get("uddg", [href])[0]
            return unquote(target)
        except (ValueError, IndexError, UnicodeError):  # CH-24: named types, not blanket
            return href
    return href


def web_search(query: str, k: int = 5, timeout_s: float = 8.0) -> list[Source]:
    """Return up to ``k`` search results for ``query`` (title, resolved url, snippet)."""
    if not query.strip():
        return []
    try:
        resp = httpx.get(
            _DDG_URL,
            params={"q": query},
            headers={"User-Agent": _UA},
            timeout=timeout_s,
            follow_redirects=True,
        )
        resp.raise_for_status()
        page = resp.text
    except httpx.HTTPError:
        # CH-24: named type — httpx.HTTPError is the base class for every exception httpx itself
        # raises (connect/timeout/transport failures, and raise_for_status()'s HTTPStatusError) —
        # a genuinely unavoidable network-I/O boundary, not a blanket catch-all.
        return []  # network down / blocked / non-2xx → caller falls back
    titles = _RESULT_A.findall(page)
    snippets = [_strip(s) for s in _SNIPPET.findall(page)]
    out: list[Source] = []
    for i, (href, title) in enumerate(titles[:k]):
        out.append(
            Source(
                title=_strip(title),
                url=_resolve_url(href),
                snippet=snippets[i] if i < len(snippets) else "",
            )
        )
    return out


def _is_public_https(url: str) -> bool:
    """SSRF guard: https only, and the host must not resolve to a private/loopback IP."""
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


def fetch_text(
    url: str,
    max_bytes: int = 40_000,
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
    if not _is_public_https(url):
        return ""
    try:
        deadline = time.monotonic() + timeout_s
        with httpx.stream(
            "GET", url, headers={"User-Agent": _UA}, timeout=timeout_s, follow_redirects=False
        ) as resp:
            if resp.status_code != 200:
                return ""
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
    except httpx.HTTPError:
        # CH-24: named type — see web_search()'s identical rationale; this streams the same
        # httpx request/response boundary (connect/timeout/transport failures mid-stream).
        return ""
    if "application/pdf" in content_type or body.startswith(b"%PDF-"):
        return _extract_pdf_text(body)[:max_chars]
    text = body.decode(resp.encoding or "utf-8", errors="ignore")
    text = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", text)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()[:max_chars]
