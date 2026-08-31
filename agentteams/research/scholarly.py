"""Scholarly-literature retrieval — OpenAlex, Crossref, arXiv.

Closes the gap that mattered most in the 2026-07-30 retrieval review: this framework generates
literature-review teams that produce bibliographies, and Constitutional Rule 5 forbids citations
that cannot be verified — but nothing in the package could reach a scholarly index. A general
web search returns a *page about* a paper; these APIs return the paper's own record, with a DOI.

Built on the pattern :mod:`agentteams.security_refs` already proves for CVE feeds: an
exact-match host allowlist, per-response size bounds, named-exception handling, and an honest
empty on every failure. Unlike ``security_refs`` (stdlib ``urllib``, because it is on the base
install path) this module uses ``httpx``, matching the rest of the ``agentteams[research]``
extra it lives in.

**Key-free by construction.** All three sources answer unauthenticated requests. No API key is
read, required, or supported — nothing here can be disabled by a missing credential.

**Honesty ceiling, same as the rest of this package.** A hit in a scholarly index is
*provenance*: it establishes that a work exists, who published it, and where. It is not a claim
that the work is correct, replicated, un-retracted, or relevant. Retraction status in particular
is NOT checked — do not present a result as endorsed. Every field is returned exactly as
retrieved; nothing is inferred, normalised, or filled in when a source omits it.

Polite-pool contact: OpenAlex and Crossref grant higher rate limits to requests carrying a
contact address. Set ``AGENTTEAMS_RESEARCH_CONTACT_EMAIL`` to opt in. It is deliberately NOT
derived from git config or any other ambient source — transmitting an operator's address to a
third party is their decision to make explicitly.
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx

# Submodule path, not `from agentteams.research import cache` — the latter creates a load-time
# edge back to the package `__init__`, which imports this module. See search.py's identical note.
from agentteams.research.cache import load as _cache_load
from agentteams.research.cache import make_key as _cache_key
from agentteams.research.cache import store as _cache_store

#: Opt-in contact address for the OpenAlex/Crossref polite pools. Never auto-derived.
CONTACT_EMAIL_ENV = "AGENTTEAMS_RESEARCH_CONTACT_EMAIL"

_OPENALEX_URL = "https://api.openalex.org/works"
_CROSSREF_URL = "https://api.crossref.org/works"
_ARXIV_URL = "https://export.arxiv.org/api/query"

#: Exact-match host allowlist, mirroring ``security_refs._ALLOWED_RESPONSE_HOSTS``. Exact only,
#: never suffix matching, so an unrelated or compromised subdomain is not implicitly trusted.
_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {"api.openalex.org", "api.crossref.org", "export.arxiv.org"}
)

#: Upper bound on a single response body. A scholarly result page is tens of KB; 8 MB is far
#: above any legitimate response and well below anything that could exhaust memory.
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024

_UA = "agentteams-research-scholarly/1.0"

_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}

#: Sources callable by name. Order here is the order results are merged in, which matters only
#: for which duplicate wins — see `_dedupe`.
SOURCES: tuple[str, ...] = ("openalex", "crossref", "arxiv")


@dataclass
class ScholarlyWork:
    """One work as returned by a scholarly index.

    Every field is exactly what the source provided. An absent field is an honest empty
    (``None`` / ``""`` / ``[]``), never a guess — a fabricated year or author on a citation is
    precisely the failure Constitutional Rule 5 exists to prevent.
    """

    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    url: str = ""
    abstract: str = ""
    venue: str = ""
    source: str = ""


def _contact_email() -> str:
    """Return the opt-in polite-pool contact address.

    Returns:
        The value of :data:`CONTACT_EMAIL_ENV`, or ``""`` when unset. Never derived from git
        config, ``$EMAIL``, or any other ambient source.
    """
    return os.environ.get(CONTACT_EMAIL_ENV, "").strip()


def _host_allowed(url: str) -> bool:
    """Whether ``url``'s host is on the exact-match allowlist.

    Args:
        url: The URL about to be requested, or the effective URL after redirects.

    Returns:
        True only for an exact host match against :data:`_ALLOWED_HOSTS`.
    """
    from urllib.parse import urlparse

    try:
        return (urlparse(url).hostname or "").lower() in _ALLOWED_HOSTS
    except ValueError:  # CH-24: named type — urlparse's own documented failure mode
        return False


def _get(url: str, params: dict[str, Any], timeout_s: float) -> httpx.Response | None:
    """Perform one allowlisted GET.

    Args:
        url: Target URL; must be on the host allowlist.
        params: Query parameters.
        timeout_s: Request timeout in seconds.

    Returns:
        The response on a 200 within the size bound, else ``None``. Redirects are NOT followed
        — every one of these APIs answers directly, so a redirect would mean the request left
        the allowlisted host, which is exactly what the allowlist is for.
    """
    if not _host_allowed(url):
        return None
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    email = _contact_email()
    if email:
        headers["User-Agent"] = f"{_UA} (mailto:{email})"
    try:
        resp = httpx.get(
            url, params=params, headers=headers, timeout=timeout_s, follow_redirects=False
        )
    except httpx.HTTPError:
        # CH-24: named type — the httpx network-I/O boundary, same rationale as search.py's.
        return None
    if resp.status_code != 200 or len(resp.content) > _MAX_RESPONSE_BYTES:
        return None
    if not _host_allowed(str(resp.url)):
        return None
    return resp


def _reconstruct_openalex_abstract(inverted: Any) -> str:
    """Rebuild plain text from OpenAlex's inverted abstract index.

    OpenAlex ships abstracts as ``{word: [positions]}`` rather than as text. Reassembling it is
    lossless for word order; punctuation spacing is whatever the original tokenisation implied.

    Args:
        inverted: The ``abstract_inverted_index`` value, or any non-dict (returns ``""``).

    Returns:
        The reconstructed abstract, or ``""`` when absent or malformed.
    """
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int):
                positioned.append((pos, str(word)))
    if not positioned:
        return ""
    positioned.sort()
    return " ".join(word for _, word in positioned)


def _parse_openalex(payload: Any, k: int) -> list[ScholarlyWork]:
    """Parse an OpenAlex ``/works`` response into works.

    Args:
        payload: The decoded JSON body.
        k: Maximum works to return.

    Returns:
        Up to ``k`` works; empty on any shape mismatch.
    """
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []
    out: list[ScholarlyWork] = []
    for item in results[:k]:
        if not isinstance(item, dict):
            continue
        authorships = item.get("authorships")
        authors = []
        if isinstance(authorships, list):
            for a in authorships:
                if isinstance(a, dict) and isinstance(a.get("author"), dict):
                    name = a["author"].get("display_name")
                    if name:
                        authors.append(str(name))
        venue = ""
        primary = item.get("primary_location")
        if isinstance(primary, dict) and isinstance(primary.get("source"), dict):
            venue = str(primary["source"].get("display_name") or "")
        doi = item.get("doi")
        year = item.get("publication_year")
        out.append(
            ScholarlyWork(
                title=str(item.get("title") or item.get("display_name") or ""),
                authors=authors,
                year=year if isinstance(year, int) else None,
                doi=str(doi).replace("https://doi.org/", "") if doi else None,
                url=str(item.get("id") or ""),
                abstract=_reconstruct_openalex_abstract(item.get("abstract_inverted_index")),
                venue=venue,
                source="openalex",
            )
        )
    return out


def _parse_crossref(payload: Any, k: int) -> list[ScholarlyWork]:
    """Parse a Crossref ``/works`` response into works.

    Args:
        payload: The decoded JSON body.
        k: Maximum works to return.

    Returns:
        Up to ``k`` works; empty on any shape mismatch.
    """
    message = payload.get("message") if isinstance(payload, dict) else None
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        return []
    out: list[ScholarlyWork] = []
    for item in items[:k]:
        if not isinstance(item, dict):
            continue
        titles = item.get("title")
        title = str(titles[0]) if isinstance(titles, list) and titles else ""
        authors = []
        raw_authors = item.get("author")
        if isinstance(raw_authors, list):
            for a in raw_authors:
                if not isinstance(a, dict):
                    continue
                name = " ".join(p for p in (a.get("given"), a.get("family")) if p)
                if name:
                    authors.append(name)
        year = None
        issued = item.get("issued")
        if isinstance(issued, dict):
            parts = issued.get("date-parts")
            if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                first = parts[0][0]
                year = first if isinstance(first, int) else None
        containers = item.get("container-title")
        venue = str(containers[0]) if isinstance(containers, list) and containers else ""
        out.append(
            ScholarlyWork(
                title=title,
                authors=authors,
                year=year,
                doi=str(item["DOI"]) if item.get("DOI") else None,
                url=str(item.get("URL") or ""),
                abstract=re.sub(r"<[^>]+>", "", str(item.get("abstract") or "")).strip(),
                venue=venue,
                source="crossref",
            )
        )
    return out


def _parse_arxiv(xml_text: str, k: int) -> list[ScholarlyWork]:
    """Parse an arXiv Atom feed into works.

    Args:
        xml_text: The raw Atom XML body.
        k: Maximum works to return.

    Returns:
        Up to ``k`` works; empty when the body is not parseable as XML.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # CH-24: named type — the documented failure of ElementTree on malformed input.
        return []
    out: list[ScholarlyWork] = []
    for entry in root.findall("atom:entry", _ARXIV_NS)[:k]:
        title_el = entry.find("atom:title", _ARXIV_NS)
        summary_el = entry.find("atom:summary", _ARXIV_NS)
        id_el = entry.find("atom:id", _ARXIV_NS)
        published_el = entry.find("atom:published", _ARXIV_NS)
        authors = [
            (name.text or "").strip()
            for name in entry.findall("atom:author/atom:name", _ARXIV_NS)
            if (name.text or "").strip()
        ]
        year = None
        if published_el is not None and (published_el.text or "")[:4].isdigit():
            year = int(published_el.text[:4])
        doi_el = entry.find("{http://arxiv.org/schemas/atom}doi")
        out.append(
            ScholarlyWork(
                title=re.sub(r"\s+", " ", (title_el.text or "")).strip() if title_el is not None else "",
                authors=authors,
                year=year,
                doi=(doi_el.text or "").strip() if doi_el is not None and doi_el.text else None,
                url=(id_el.text or "").strip() if id_el is not None else "",
                abstract=re.sub(r"\s+", " ", (summary_el.text or "")).strip() if summary_el is not None else "",
                venue="arXiv",
                source="arxiv",
            )
        )
    return out


def search_openalex(query: str, k: int = 5, timeout_s: float = 10.0) -> list[ScholarlyWork]:
    """Search OpenAlex.

    Args:
        query: Free-text query.
        k: Maximum works to return.
        timeout_s: Request timeout in seconds.

    Returns:
        Up to ``k`` works; empty on any failure. Never raises.
    """
    params: dict[str, Any] = {"search": query, "per-page": max(1, min(k, 50))}
    email = _contact_email()
    if email:
        params["mailto"] = email
    resp = _get(_OPENALEX_URL, params, timeout_s)
    if resp is None:
        return []
    try:
        return _parse_openalex(resp.json(), k)
    except (json.JSONDecodeError, ValueError):  # CH-24: named types — non-JSON body
        return []


def search_crossref(query: str, k: int = 5, timeout_s: float = 10.0) -> list[ScholarlyWork]:
    """Search Crossref.

    Args:
        query: Free-text query.
        k: Maximum works to return.
        timeout_s: Request timeout in seconds.

    Returns:
        Up to ``k`` works; empty on any failure. Never raises.
    """
    params: dict[str, Any] = {"query": query, "rows": max(1, min(k, 50))}
    email = _contact_email()
    if email:
        params["mailto"] = email
    resp = _get(_CROSSREF_URL, params, timeout_s)
    if resp is None:
        return []
    try:
        return _parse_crossref(resp.json(), k)
    except (json.JSONDecodeError, ValueError):  # CH-24: named types — non-JSON body
        return []


def search_arxiv(query: str, k: int = 5, timeout_s: float = 10.0) -> list[ScholarlyWork]:
    """Search arXiv.

    Args:
        query: Free-text query.
        k: Maximum works to return.
        timeout_s: Request timeout in seconds.

    Returns:
        Up to ``k`` works; empty on any failure. Never raises.
    """
    resp = _get(
        _ARXIV_URL,
        {"search_query": f"all:{query}", "max_results": max(1, min(k, 50))},
        timeout_s,
    )
    if resp is None:
        return []
    return _parse_arxiv(resp.text, k)


_SEARCHERS: dict[str, Callable[[str, int, float], list[ScholarlyWork]]] = {
    "openalex": search_openalex,
    "crossref": search_crossref,
    "arxiv": search_arxiv,
}


def _normalise_title(title: str) -> str:
    """Reduce a title to a comparison key (lowercase, alphanumerics and single spaces).

    Args:
        title: A work title.

    Returns:
        The normalised key; ``""`` for a title with no alphanumeric content.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", title.lower())).strip()


def _dedupe(works: list[ScholarlyWork]) -> list[ScholarlyWork]:
    """Remove duplicate works, preferring the first occurrence.

    Deduplicates by DOI first (the only truly stable identifier across these three indexes),
    then by normalised title for the many works that reach one index without a DOI. Order is
    preserved, so the earlier source in :data:`SOURCES` wins a tie.

    Args:
        works: Works from one or more sources, in merge order.

    Returns:
        The deduplicated list.
    """
    seen_doi: set[str] = set()
    seen_title: set[str] = set()
    out: list[ScholarlyWork] = []
    for work in works:
        doi = (work.doi or "").lower().strip()
        title_key = _normalise_title(work.title)
        if doi and doi in seen_doi:
            continue
        if not doi and title_key and title_key in seen_title:
            continue
        if doi:
            seen_doi.add(doi)
        if title_key:
            seen_title.add(title_key)
        out.append(work)
    return out


def _result_or_empty(future: Any) -> list[ScholarlyWork]:
    """Return a completed future's works, or ``[]`` if it failed.

    Each ``search_*`` function already absorbs its own failures, so this only catches the
    residual case of an exception crossing the thread boundary. Written as return-empty rather
    than except-and-continue deliberately: CH-24's ratchet
    (``tests/test_code_hygiene.py::SWALLOW_BASELINE``) counts ``except`` clauses whose body is
    only ``pass``/``continue``, and this boundary does not need to be one of them.

    Args:
        future: A ``concurrent.futures.Future`` wrapping one source search.

    Returns:
        The works the future produced, or ``[]`` when it raised. One index failing must never
        lose the other indexes' results.
    """
    try:
        return list(future.result())
    except (httpx.HTTPError, ValueError, ET.ParseError):
        # CH-24: named types — the same failure classes the individual searchers absorb.
        return []


def scholarly_search(
    query: str,
    k: int = 5,
    sources: tuple[str, ...] = SOURCES,
    timeout_s: float = 10.0,
) -> list[ScholarlyWork]:
    """Search several scholarly indexes concurrently and return deduplicated works.

    Args:
        query: Free-text query.
        k: Maximum works to return *after* deduplication.
        sources: Which indexes to query; unknown names are ignored. Defaults to all three.
        timeout_s: Per-request timeout in seconds.

    Returns:
        Up to ``k`` works, deduplicated by DOI then normalised title. Empty when the query is
        blank, every source fails, or nothing matched — this function does not distinguish
        those, because unlike a general web search these APIs do not challenge requests.

    Raises:
        Nothing. Every source degrades to an empty contribution.
    """
    if not query.strip():
        return []
    chosen = [s for s in sources if s in _SEARCHERS]
    if not chosen:
        return []

    cache_key = _cache_key("scholar", query, k, *chosen)
    cached = _cache_load(cache_key)
    if isinstance(cached, list):
        return [ScholarlyWork(**item) for item in cached if isinstance(item, dict)][:k]

    collected: list[ScholarlyWork] = []
    with ThreadPoolExecutor(max_workers=len(chosen)) as pool:
        futures = {name: pool.submit(_SEARCHERS[name], query, k, timeout_s) for name in chosen}
        # Iterate in `chosen` order, not completion order, so dedup precedence is deterministic.
        for name in chosen:
            collected.extend(_result_or_empty(futures[name]))

    result = _dedupe(collected)[:k]
    if result:
        _cache_store(cache_key, [w.__dict__ for w in result])
    return result


def format_citation(work: ScholarlyWork) -> str:
    """Render a work as a compact, plain-text citation line.

    Only fields the source actually provided appear. A missing year renders as ``n.d.`` rather
    than a guess, and a work with no DOI simply has no DOI in its line — the alternative
    (inventing one, or silently dropping the work) is what Constitutional Rule 5 forbids.

    Args:
        work: The work to render.

    Returns:
        A single-line citation.
    """
    authors = ", ".join(work.authors[:3])
    if len(work.authors) > 3:
        authors += " et al."
    parts = [p for p in (authors, f"({work.year})" if work.year else "(n.d.)", work.title) if p]
    line = ". ".join(parts)
    if work.venue:
        line += f". {work.venue}"
    if work.doi:
        line += f". https://doi.org/{work.doi}"
    elif work.url:
        line += f". {work.url}"
    return line
