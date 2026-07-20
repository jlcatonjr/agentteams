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

Deliberately never uses the word "primary" for this concept — :class:`agentteams.research.
reputable.AllowlistConfig`'s ``tier="primary"`` already means something else (a repository of
original source *texts*, e.g. gutenberg.org). "Contemporaneous account" is used instead.
"""

from __future__ import annotations

from typing import Literal

from agentteams.research.reputable import ReputableSource

PerspectiveKind = Literal["reported", "contested"]
"""``"reported"`` — a plain factual claim a news source is the origin of (what happened).
``"contested"`` — a claim about how a source *characterized* something (e.g. an outlet's
editorializing description of a person or event), which deserves more hedging than a bare factual
report. This module doesn't decide which applies to a given claim — that judgment needs the
claim's own text, which this module never sees — it only exports the shared vocabulary so callers
across this framework use the same two labels rather than independently-invented near-synonyms.
"""


def is_news_source(source: ReputableSource) -> bool:
    """``True`` when ``source`` is tagged ``type="news"``. Exists so callers don't hardcode the
    string literal ``"news"`` in more than one place."""
    return source.type == "news"


def perspective_attribution(source: ReputableSource, published_at: str | None) -> str:
    """The single, shared place that formats a consistent attribution string for a news-typed
    source — every consumer (researchteam's docs, a downstream product's prompt construction)
    references or mirrors this wording rather than inventing its own.

    Degrades honestly when no date was extractable — never fabricates one.
    """
    when = f" ({published_at})" if published_at else " (date not available)"
    return f"{source.domain} reported{when}"
