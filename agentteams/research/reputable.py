"""Reputable-source selection — a curated allowlist + primary-source steering.

A topic is turned into targeted ``site:`` searches against primary-source repositories plus an
allowlist-filtered general search, and only allowlisted domains are ever returned.

Honesty ceiling: "reputable" here means **provenance-vetted by a curated allowlist**, NOT a
guarantee the content is correct. Retrieval is navigation, never a citation anchor — a caller
must keep that framing.

Ported and generalized from LingoFriend (``knowledge/reputable.py``): the *mechanism* here is
domain-neutral; LingoFriend's own allowlist data (tuned for a Romanian-language conversational
tutor) does not travel — every consumer supplies its own :class:`AllowlistConfig`, or uses
:data:`DEFAULT_CONFIG`, a small, deliberately generic starting point (see its own docstring for
what it is and is not).
"""

from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlparse

from agentteams.research.search import Source, web_search

# Values `type_by_domain` may take. Additive to `tier` — describes *what kind of thing* a domain
# is, never how much to trust it (that's `tier`'s job). A domain absent from `type_by_domain`
# resolves to "unclassified" rather than raising, so a config can lag without breaking.
VALID_TYPES = frozenset(
    {"news", "academic", "government", "encyclopedia", "primary-text", "book"}
)

#: Ceiling on simultaneous search requests issued for one topic. Four keeps the common case
#: (a handful of primary repos plus the general backstop) effectively parallel while refusing
#: to scale the burst linearly with allowlist size.
_MAX_CONCURRENT_SEARCHES = 4


@dataclass(frozen=True)
class AllowlistConfig:
    """The full, data-driven shape a :class:`ReputableSourceAllowlist` is built from.

    Two independent axes per domain: `tier_by_domain` (how much to trust it — ranked by
    `tier_rank`) and `type_by_domain` (what kind of source it is — informational only, never
    affects ranking). `topic_primary_repos` routes a topic's keyword stems to the primary
    repositories worth a targeted ``site:`` search; `path_scope` optionally restricts a domain to
    a URL-path prefix (for domains too broad to allowlist wholesale). `default_repos` is the
    fallback repo set used when no topic keyword matches.
    """

    tier_by_domain: dict[str, str]
    type_by_domain: dict[str, str] = field(default_factory=dict)
    topic_primary_repos: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = ()
    path_scope: dict[str, str] = field(default_factory=dict)
    tier_rank: dict[str, int] = field(default_factory=lambda: {"authoritative": 0, "reference": 1, "primary": 2})
    default_repos: tuple[str, ...] = ()


# A small, deliberately generic starting point — NOT a comprehensive claim about source quality
# for any one domain, and NOT tuned for any particular subject area or language. Ship this as a
# convenience default; a real consuming project should supply its own AllowlistConfig sized to
# its own domain and editorial judgment. See the module docstring's honesty ceiling.
DEFAULT_CONFIG = AllowlistConfig(
    tier_by_domain={
        "wikipedia.org": "reference",
        "reuters.com": "authoritative",
        "apnews.com": "authoritative",
        "bbc.com": "authoritative",
    },
    type_by_domain={
        "wikipedia.org": "encyclopedia",
        "reuters.com": "news",
        "apnews.com": "news",
        "bbc.com": "news",
    },
    default_repos=(),
)


# ---------------------------------------------------------------------------
# Archetype presets
#
# DEFAULT_CONFIG above is four general-interest domains with no primary repositories — a
# demonstration, not a working configuration. In practice `reputable_sources()` on the default
# reduced to "one general search filtered to Wikipedia and three wire services", which is not
# usable for technical or scholarly work.
#
# The presets below are larger starting points for the three project archetypes this module
# actually gets used from. The SAME honesty ceiling stated in the module docstring applies to
# every one of them, and is worth restating because a longer list reads as more authoritative
# than a short one: these are PROVENANCE judgments — "this domain is a defensible place to
# look" — never claims that a given page is correct, current, or unbiased. A consuming project
# is expected to edit these, not inherit them uncritically.
#
# DEFAULT_CONFIG is deliberately left exactly as it was: it is the default argument of
# ReputableSourceAllowlist.__init__, so changing its contents would silently change behaviour
# for every existing caller.
# ---------------------------------------------------------------------------

SOFTWARE_CONFIG = AllowlistConfig(
    tier_by_domain={
        "python.org": "authoritative",
        "developer.mozilla.org": "authoritative",
        "rust-lang.org": "authoritative",
        "go.dev": "authoritative",
        "postgresql.org": "authoritative",
        "kernel.org": "authoritative",
        "w3.org": "authoritative",
        "ietf.org": "authoritative",
        "docs.github.com": "authoritative",
        "readthedocs.io": "reference",
        "github.com": "primary",
        "stackoverflow.com": "reference",
        "wikipedia.org": "reference",
    },
    type_by_domain={
        "python.org": "primary-text",
        "developer.mozilla.org": "primary-text",
        "rust-lang.org": "primary-text",
        "go.dev": "primary-text",
        "postgresql.org": "primary-text",
        "kernel.org": "primary-text",
        "w3.org": "government",
        "ietf.org": "government",
        "docs.github.com": "primary-text",
        "readthedocs.io": "primary-text",
        "github.com": "primary-text",
        "stackoverflow.com": "encyclopedia",
        "wikipedia.org": "encyclopedia",
    },
    topic_primary_repos=(
        (("python", "pip", "pypi", "django", "flask", "pytest"), ("python.org", "readthedocs.io")),
        (("javascript", "typescript", "npm", "node", "react"), ("developer.mozilla.org", "github.com")),
        (("rust", "cargo", "crate"), ("rust-lang.org", "github.com")),
        (("golang", "go module"), ("go.dev", "github.com")),
        (("sql", "postgres", "postgresql", "database"), ("postgresql.org",)),
        (("http", "tls", "protocol", "rfc"), ("ietf.org", "w3.org")),
    ),
    default_repos=("github.com", "readthedocs.io"),
)
"""Software-engineering sources. Provenance only — see the block comment above."""

RESEARCH_CONFIG = AllowlistConfig(
    tier_by_domain={
        "doi.org": "authoritative",
        "arxiv.org": "primary",
        "openalex.org": "reference",
        "crossref.org": "reference",
        "semanticscholar.org": "reference",
        "pubmed.ncbi.nlm.nih.gov": "authoritative",
        "ncbi.nlm.nih.gov": "authoritative",
        "nature.com": "authoritative",
        "science.org": "authoritative",
        "jstor.org": "reference",
        "plato.stanford.edu": "authoritative",
        "gutenberg.org": "primary",
        "wikipedia.org": "reference",
    },
    type_by_domain={
        "doi.org": "academic",
        "arxiv.org": "academic",
        "openalex.org": "academic",
        "crossref.org": "academic",
        "semanticscholar.org": "academic",
        "pubmed.ncbi.nlm.nih.gov": "academic",
        "ncbi.nlm.nih.gov": "government",
        "nature.com": "academic",
        "science.org": "academic",
        "jstor.org": "academic",
        "plato.stanford.edu": "encyclopedia",
        "gutenberg.org": "primary-text",
        "wikipedia.org": "encyclopedia",
    },
    topic_primary_repos=(
        (("physics", "mathematics", "machine learning", "computer science"), ("arxiv.org",)),
        (("medicine", "clinical", "biology", "genome"), ("pubmed.ncbi.nlm.nih.gov",)),
        (("philosophy", "ethics", "epistemology"), ("plato.stanford.edu",)),
    ),
    default_repos=("openalex.org", "crossref.org"),
)
"""Scholarly sources. Pairs with :mod:`agentteams.research.scholarly`, which queries the
key-free subset of these directly rather than through a general web search. Provenance only."""

DATA_CONFIG = AllowlistConfig(
    tier_by_domain={
        "data.gov": "authoritative",
        "census.gov": "authoritative",
        "bls.gov": "authoritative",
        "federalreserve.gov": "authoritative",
        "fred.stlouisfed.org": "authoritative",
        "eurostat.ec.europa.eu": "authoritative",
        "worldbank.org": "authoritative",
        "oecd.org": "authoritative",
        "imf.org": "authoritative",
        "wikipedia.org": "reference",
    },
    type_by_domain={
        "data.gov": "government",
        "census.gov": "government",
        "bls.gov": "government",
        "federalreserve.gov": "government",
        "fred.stlouisfed.org": "government",
        "eurostat.ec.europa.eu": "government",
        "worldbank.org": "government",
        "oecd.org": "government",
        "imf.org": "government",
        "wikipedia.org": "encyclopedia",
    },
    topic_primary_repos=(
        (("employment", "unemployment", "labor", "wage"), ("bls.gov", "fred.stlouisfed.org")),
        (("population", "demographic", "census"), ("census.gov",)),
        (("gdp", "inflation", "monetary", "interest rate"), ("fred.stlouisfed.org", "federalreserve.gov")),
        (("global", "developing", "international"), ("worldbank.org", "oecd.org", "imf.org")),
    ),
    default_repos=("data.gov", "worldbank.org"),
)
"""Official-statistics sources. Provenance only — an official series is authoritative about
what it measured, which is not the same as being the right series for a question."""

#: `project_type` values (see `agentteams.analyze.classify_project_type`) → preset.
#: Unlisted types intentionally fall back to DEFAULT_CONFIG rather than guessing.
_CONFIG_BY_PROJECT_TYPE: dict[str, AllowlistConfig] = {
    "software": SOFTWARE_CONFIG,
    "research": RESEARCH_CONFIG,
    "writing": RESEARCH_CONFIG,
    "documentation": SOFTWARE_CONFIG,
    "data-pipeline": DATA_CONFIG,
}


def config_for_project_type(project_type: str) -> AllowlistConfig:
    """Return the allowlist preset matching a project type.

    Args:
        project_type: A value from ``agentteams.analyze.classify_project_type`` — e.g.
            ``"software"``, ``"research"``, ``"data-pipeline"``.

    Returns:
        The matching preset, or :data:`DEFAULT_CONFIG` for an unknown, ``"mixed"``, or
        ``"unknown"`` type. Never raises: an unrecognised type means "no better information
        than the generic default", not an error.
    """
    return _CONFIG_BY_PROJECT_TYPE.get((project_type or "").strip().lower(), DEFAULT_CONFIG)


@dataclass
class ReputableSource:
    """A search hit from an allowlisted domain, tagged with its reputability tier and type."""

    title: str
    url: str
    snippet: str
    domain: str
    tier: str
    type: str = "unclassified"
    license: str | None = None


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


class ReputableSourceAllowlist:
    """Stateless-per-call wrapper around an :class:`AllowlistConfig`.

    Construct once with a config (or use :data:`DEFAULT_CONFIG`) and call
    :meth:`reputable_sources`/:meth:`tier_of` as needed.
    """

    def __init__(self, config: AllowlistConfig = DEFAULT_CONFIG) -> None:
        self._config = config

    def _registered_domain(self, host: str) -> str | None:
        """Return the allowlist key a host matches (handles subdomains).

        Prefers the LONGEST (most specific) matching domain when more than one matches — a plain
        first-match loop is order-dependent on dict insertion order, which silently misresolves a
        subdomain that happens to also be a suffix of an unrelated, shorter, already-listed
        parent domain (e.g. a hypothetical ``sub.example.org`` when both ``example.org`` and
        ``sub.example.org`` are listed — first-match-wins could return the wrong one depending on
        dict order).
        """
        host = host.lower().lstrip(".")
        if host.startswith("www."):
            host = host[4:]
        matches = [d for d in self._config.tier_by_domain if host == d or host.endswith("." + d)]
        return max(matches, key=len) if matches else None

    def _primary_repos_for(self, topic: str) -> tuple[tuple[str, ...], bool]:
        """Return ``(repos, is_fallback)`` — ``is_fallback`` is True only when no subject-area
        keyword matched and the generic `default_repos` set was used."""
        low = topic.lower()
        for keywords, repos in self._config.topic_primary_repos:
            if any(kw in low for kw in keywords):
                return repos, False
        return self._config.default_repos, True

    def _is_relevant(self, hit: Source, topic: str) -> bool:
        """ALL significant query terms (len >= 4, case/diacritic-folded) must appear in the
        hit's title or snippet. Only applied to fallback-repo site: searches — never to
        keyword-matched primary repos or the general backstop search, both already relevant by
        construction."""
        terms = [t for t in re.findall(r"\w+", _fold(topic)) if len(t) >= 4]
        if not terms:
            return True
        haystack = _fold(hit.title + " " + hit.snippet)
        return all(term in haystack for term in terms)

    def _to_reputable(self, hit: Source) -> ReputableSource | None:
        """Keep a hit only if its domain is on the allowlist and, for a path-scoped domain, its
        URL path falls within that scope; tag it with the domain's tier and type."""
        try:
            parsed = urlparse(hit.url)
            host = parsed.hostname or ""
        except ValueError:  # CH-24: named type — urlparse's own documented failure mode
            return None
        domain = self._registered_domain(host)
        if domain is None:
            return None
        scope = self._config.path_scope.get(domain)
        if scope is not None and not parsed.path.startswith(scope):
            return None
        return ReputableSource(
            title=hit.title,
            url=hit.url,
            snippet=hit.snippet,
            domain=domain,
            tier=self._config.tier_by_domain[domain],
            type=self._config.type_by_domain.get(domain, "unclassified"),
        )

    def reputable_sources(
        self, topic: str, k: int = 3, timeout_s: float = 8.0
    ) -> list[ReputableSource]:
        """Return up to ``k`` allowlisted sources for ``topic``, ranked per ``tier_rank``.

        Best-effort: runs a targeted ``site:`` search for each of the topic's primary
        repositories (``site:`` is honored per-domain by some search backends and not others —
        empty results are simply skipped) plus one allowlist-filtered general search. Issued
        concurrently, not sequentially, so worst-case latency stays bounded by the slowest single
        call rather than multiplying by call count. Deduped by URL. Returns ``[]`` honestly when
        nothing reputable is found — never a mislabelled source.
        """
        if not topic.strip():
            return []
        seen: set[str] = set()
        out: list[ReputableSource] = []

        def _add(hits: list[Source], require_relevant: bool = False) -> None:
            for hit in hits:
                if require_relevant and not self._is_relevant(hit, topic):
                    continue
                rep = self._to_reputable(hit)
                if rep is None or rep.url in seen:
                    continue
                seen.add(rep.url)
                out.append(rep)

        repos, is_fallback = self._primary_repos_for(topic)
        jobs: list[tuple[str, int, bool]] = [
            (f"site:{repo} {topic}", 3, is_fallback) for repo in repos
        ]
        jobs.append((topic, 8, False))

        # Bounded, not one-worker-per-job. A config with many primary repos previously fired
        # `len(repos) + 1` simultaneous requests at the same free endpoint for a single topic;
        # that burst is itself a plausible contributor to the challenge responses this package
        # has to work around (see agentteams.research.backends). Capping trades a little
        # latency for a materially lower chance of the whole batch being deflected.
        with ThreadPoolExecutor(max_workers=min(len(jobs), _MAX_CONCURRENT_SEARCHES)) as pool:
            futures = {
                pool.submit(web_search, query, count, timeout_s): require_relevant
                for query, count, require_relevant in jobs
            }
            for future in futures:
                try:
                    _add(future.result(), require_relevant=futures[future])
                except Exception:  # noqa: BLE001 — CH-24: thread-isolation boundary.
                    # web_search() itself already degrades every failure to [] and never raises,
                    # so this guards only genuinely unexpected concurrent.futures-level failures
                    # (e.g. CancelledError) — one worker's unexpected failure must not abort the
                    # whole batch, matching this repo's existing best-effort-mapper precedent
                    # (architecture.py: skip an unparseable file rather than abort the whole map).
                    continue

        out.sort(key=lambda s: self._config.tier_rank.get(s.tier, 99))
        return out[:k]

    def tier_of(self, url: str) -> str | None:
        """Public helper: the reputability tier of a URL's domain, or None if not allowlisted."""
        try:
            domain = self._registered_domain(urlparse(url).hostname or "")
        except ValueError:  # CH-24: named type — urlparse's own documented failure mode
            return None
        return self._config.tier_by_domain.get(domain) if domain else None
