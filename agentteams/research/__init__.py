"""Research + fact-verification — an optional runtime capability (``pip install agentteams[research]``).

Unlike the rest of the ``agentteams`` package, this subpackage is a real, importable Python
library a consuming project's own runtime may depend on directly — not a design-time template.
It has no import-time dependency on, and no importer within, ``agentteams``'s CLI/generator
pipeline (``analyze``, ``render``, ``build_team``). See ``docs_src/api-reference/research.md`` and
``tmp/by-week/2026-W29/agentteams-research-verification-baseline.plan.md`` (Design §0) for the
disclosed boundary this crosses and why.

Honesty ceiling, restated at the package level (see each module for detail): allowlisted-domain
retrieval is provenance, not correctness — "reputable" is never "true." Claim-verification
verdicts are "survived" or "refuted" — never "verified" or "proven."
"""

from __future__ import annotations

from agentteams.research.backends import available_backends, backend_names
from agentteams.research.news import (
    PerspectiveKind,
    is_news_source,
    perspective_attribution,
)
from agentteams.research.reputable import (
    DATA_CONFIG,
    DEFAULT_CONFIG,
    RESEARCH_CONFIG,
    SOFTWARE_CONFIG,
    AllowlistConfig,
    ReputableSource,
    ReputableSourceAllowlist,
    config_for_project_type,
)
from agentteams.research.scholarly import (
    ScholarlyWork,
    format_citation,
    scholarly_search,
    search_arxiv,
    search_crossref,
    search_openalex,
)
from agentteams.research.search import (
    SearchProvenance,
    Source,
    extract_published_date,
    fetch_text,
    fetch_text_and_date,
    web_search,
    web_search_verbose,
    web_search_with_provenance,
)
from agentteams.research.verify import (
    ChatFn,
    Claim,
    Verdict,
    audit_claims,
    extract_claims,
    revise,
)

__all__ = [
    "DATA_CONFIG",
    "DEFAULT_CONFIG",
    "RESEARCH_CONFIG",
    "SOFTWARE_CONFIG",
    "AllowlistConfig",
    "ChatFn",
    "Claim",
    "PerspectiveKind",
    "ReputableSource",
    "ReputableSourceAllowlist",
    "ScholarlyWork",
    "SearchProvenance",
    "Source",
    "Verdict",
    "audit_claims",
    "available_backends",
    "backend_names",
    "config_for_project_type",
    "extract_claims",
    "extract_published_date",
    "fetch_text",
    "fetch_text_and_date",
    "format_citation",
    "is_news_source",
    "perspective_attribution",
    "revise",
    "scholarly_search",
    "search_arxiv",
    "search_crossref",
    "search_openalex",
    "web_search",
    "web_search_verbose",
    "web_search_with_provenance",
]
