"""News as perspective — attribution, not correctness.

News is a contemporaneous account of *perspective* on an event — not verified fact, and not the
same epistemic class as an encyclopedia or government source. Multiple outlets may report the
same event differently; this module does not adjudicate between them, it only helps attribute
what one outlet said.

This module gives the ``type="news"`` tag (already present in
:mod:`agentteams.research.reputable`, previously inert — stored and returned but never read for
behavior) its first real behavior: a consistent attribution string a caller can present instead of
stating a news claim as settled fact. Publish-date extraction itself lives in
:mod:`agentteams.research.search` (:func:`~agentteams.research.search.extract_published_date`) —
that is a content-parsing concern, the same category as that module's own HTML-stripping/PDF
extraction, and keeping it there avoids an import cycle this module would otherwise create
(``search`` -> ``news`` -> ``reputable`` -> ``search``, since :mod:`agentteams.research.reputable`
already imports from :mod:`agentteams.research.search`).

Perspective coverage (:class:`PerspectiveSet`, :func:`gather_perspectives`,
:func:`perspective_summary`) records whether a ``"contested"`` claim has more than one allowlisted
news outlet behind it, so a consumer cannot present one outlet's characterisation as *the*
account. :func:`gather_perspectives` is the one function here that sees a claim's text — it uses
it only as a search query and to check that a hit's snippet shares the claim's terms; it still
never judges whether the claim is true. The other functions never see claim text. Searching is
injected (``search_fn``) so the core stays network-free, and only :mod:`agentteams.research.
reputable` names are imported here, keeping the import cycle described above closed.

Deliberately never uses the word "primary" for this concept — :class:`agentteams.research.
reputable.AllowlistConfig`'s ``tier="primary"`` already means something else (a repository of
original source *texts*, e.g. gutenberg.org). "Contemporaneous account" is used instead.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import httpx

from agentteams.research.reputable import (
    DEFAULT_CONFIG,
    ReputableSource,
    ReputableSourceAllowlist,
)

if TYPE_CHECKING:
    from agentteams.research.search import Source

Coverage = Literal["multi-outlet", "single-source"]
"""Whether a :class:`PerspectiveSet` has two or more distinct outlets (``"multi-outlet"``)."""

#: Words too common in news prose to show that a hit covers the same claim. Terms shorter than
#: four characters are already dropped (the same length floor as ``reputable``'s relevance check).
_STOPWORDS = frozenset(
    {
        "about", "after", "also", "been", "before", "being", "could", "from", "have", "into",
        "more", "over", "said", "says", "than", "that", "their", "them", "then", "there",
        "they", "this", "were", "what", "when", "which", "while", "will", "with", "would",
    }
)

#: A hit must share at least this many of the claim's content words (or all of them, when the
#: claim has fewer) before it counts as another outlet covering the claim.
_MIN_SHARED_TERMS = 2

PerspectiveKind = Literal["reported", "contested"]
"""``"reported"`` — a plain factual claim a news source is the origin of (what happened).
``"contested"`` — a claim about how a source *characterized* something (e.g. an outlet's
editorializing description of a person or event), which deserves more hedging than a bare factual
report. This module doesn't decide which applies to a given claim — that judgment is the
caller's (even :func:`gather_perspectives`, which sees the claim text, takes ``kind`` as given) —
it only exports the shared vocabulary so callers
across this framework use the same two labels rather than independently-invented near-synonyms.
"""


def is_news_source(source: ReputableSource) -> bool:
    """``True`` when ``source`` is tagged ``type="news"``. Exists so callers don't hardcode the
    string literal ``"news"`` in more than one place."""
    return source.type == "news"


def perspective_attribution(source: ReputableSource, published_at: str | None) -> str:
    """The single, shared place that formats a consistent attribution string for a news-typed
    source — every consumer (example-research's docs, a downstream product's prompt construction)
    references or mirrors this wording rather than inventing its own.

    Degrades honestly when no date was extractable — never fabricates one.
    """
    return f"{source.domain} reported{_when(published_at)}"


def _when(published_at: str | None) -> str:
    return f" ({published_at})" if published_at else " (date not available)"


def _content_terms(text: str) -> set[str]:
    """Case- and diacritic-folded words of four or more characters, minus :data:`_STOPWORDS`."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    return {t for t in re.findall(r"\w+", folded) if len(t) >= 4 and t not in _STOPWORDS}


def _shares_claim_terms(claim_terms: set[str], snippet: str) -> bool:
    """``True`` when ``snippet`` contains enough of the claim's content words (see
    :data:`_MIN_SHARED_TERMS`). A claim with no content words matches nothing — a hit cannot be
    shown to cover an empty claim."""
    if not claim_terms:
        return False
    needed = min(_MIN_SHARED_TERMS, len(claim_terms))
    return len(claim_terms & _content_terms(snippet)) >= needed


@dataclass(frozen=True)
class PerspectiveSet:
    """The outlets behind one news claim — attribution, never a judgment of the claim.

    Attributes:
        claim: The claim text, as the caller stated it.
        kind: ``"reported"`` or ``"contested"`` (see :data:`PerspectiveKind`).
        outlets: ``(domain, published_at)`` pairs, the first outlet first. ``published_at`` is
            ``None`` when no date was available — never a fabricated one.
    """

    claim: str
    kind: PerspectiveKind
    outlets: tuple[tuple[str, str | None], ...]

    @property
    def coverage(self) -> Coverage:
        """``"multi-outlet"`` when two or more distinct allowlisted domains are present,
        otherwise ``"single-source"``."""
        return "multi-outlet" if len({d for d, _ in self.outlets}) >= 2 else "single-source"


def perspective_summary(ps: PerspectiveSet) -> str:
    """Format the attribution a consumer presents for a :class:`PerspectiveSet`.

    Never states the claim as fact. A ``"reported"`` set uses the :func:`perspective_attribution`
    wording for its first outlet. A ``"contested"`` set always says it is contested, and says
    either that no second outlet was found or which other outlets *also covered* it — "covered",
    not "reported", because a matching search hit shows the outlet wrote about the claim, not
    that it gave the same account.

    Args:
        ps: The set to summarise.

    Returns:
        A one-line attribution string.

    Raises:
        ValueError: ``ps.outlets`` is empty — there is nothing to attribute.
    """
    if not ps.outlets:
        raise ValueError("PerspectiveSet has no outlets to attribute")
    first_domain, first_date = ps.outlets[0]
    lead = f"{first_domain} reported{_when(first_date)}"
    if ps.kind != "contested":
        return lead
    if ps.coverage == "single-source":
        return f"{lead}; contested characterization, single source: no second outlet found"
    others = ", ".join(f"{d}{_when(date)}" for d, date in ps.outlets[1:])
    return f"{lead}; also covered by {others}; contested characterization, outlets may differ"


def gather_perspectives(
    claim: str,
    first: ReputableSource,
    published_at: str | None,
    *,
    kind: PerspectiveKind = "contested",
    search_fn: Callable[[str], Sequence[Source]] | None = None,
    allowlist: ReputableSourceAllowlist | None = None,
    k: int = 5,
) -> PerspectiveSet:
    """Collect the allowlisted news outlets behind a claim, starting from the one that made it.

    For a ``"contested"`` claim with a ``search_fn``, searches the claim text and keeps a hit
    only when the allowlist classifies it as a ``type="news"`` source, its domain differs from
    ``first.domain`` and every outlet already kept, and its snippet shares the claim's content
    words (two or more, or all of them when the claim has fewer). Search hits carry no publish
    date, so added outlets have ``published_at=None``. A ``"reported"`` claim, or no
    ``search_fn``, is never searched.

    Args:
        claim: The claim's text; used as the search query and for the snippet-overlap check.
        first: The outlet the claim came from.
        published_at: ``first``'s publish date, or ``None`` when unknown.
        kind: ``"contested"`` (default) or ``"reported"``.
        search_fn: Caller-supplied search, e.g. :func:`agentteams.research.search.web_search`.
            ``None`` (default) performs no search.
        allowlist: Classifies hits; defaults to one built on
            :data:`~agentteams.research.reputable.DEFAULT_CONFIG`.
        k: Maximum number of additional outlets to add.

    Returns:
        A :class:`PerspectiveSet` with ``first`` as its first outlet.

    Raises:
        Nothing for search failures: an ``httpx.HTTPError`` or ``OSError`` from ``search_fn``
        leaves the set single-source. Any other exception from ``search_fn`` propagates.
    """
    outlets: list[tuple[str, str | None]] = [(first.domain, published_at)]
    if kind != "contested" or search_fn is None or k <= 0:
        return PerspectiveSet(claim=claim, kind=kind, outlets=tuple(outlets))
    classifier = allowlist if allowlist is not None else ReputableSourceAllowlist(DEFAULT_CONFIG)
    try:
        hits = list(search_fn(claim))
    except (httpx.HTTPError, OSError):  # CH-24: named transport failures — degrade to single-source
        return PerspectiveSet(claim=claim, kind=kind, outlets=tuple(outlets))
    claim_terms = _content_terms(claim)
    seen = {first.domain}
    for hit in hits:
        if len(outlets) > k:
            break
        rep = classifier.classify(hit)
        if rep is None or not is_news_source(rep) or rep.domain in seen:
            continue
        if not _shares_claim_terms(claim_terms, hit.snippet):
            continue
        seen.add(rep.domain)
        outlets.append((rep.domain, None))
    return PerspectiveSet(claim=claim, kind=kind, outlets=tuple(outlets))
