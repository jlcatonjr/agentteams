"""freshness.py — operator-invoked search for newly disclosed AI-adversary techniques.

Improvement item I2 (references/plans/redteam-osint-ost-benchmark-improvements.csv,
High priority): the corpus is entirely hand-authored and reactive to internal
incidents, with no mechanism to notice external technique disclosures. This module
reuses :mod:`agentteams.research.search` (already-built, OSINT-adjacent tooling that
sits completely disconnected from the redteam module — the report's sharpest finding)
to surface CANDIDATE strings for human triage.

Explicitly NOT wired into the standing weekly cron or ``cycle.py``'s 7-phase run: that
job must stay network-independent and deterministic. This is a separate,
operator-invoked-only command (``--redteam-freshness-check``).

Explicitly does NOT attempt automated corroboration via :mod:`agentteams.research.verify`
— that module's dual-lens verification requires a caller-supplied LLM ``ChatFn``
(``agentteams/research/verify.py`` has no hardcoded model client by design), which this
CLI context has no plumbing for. Building that plumbing would be real scope growth
beyond this item's M-L effort estimate. Raw search results are surfaced directly for a
human to read and judge — a smaller, honest scope, and still squarely "measures and
reports," never "remediates": nothing here writes to ``registry.py`` or the battery.

Query set is small and fixed (bounded, declared) rather than open-ended, so this stays
inside the same "no agent loop or external API call without a declared rate limit or
termination condition" discipline the security template itself asks of AI-authored code
(LLM10, Unbounded Consumption).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Small, fixed, declared query set — not open-ended (LLM10 discipline, see module docstring).
DEFAULT_QUERIES: tuple[str, ...] = (
    "new prompt injection technique disclosed 2026",
    "AI agent jailbreak technique 2026",
    "MITRE ATLAS new adversarial ML technique",
    "LLM agent excessive agency exploit 2026",
)

CANDIDATES_NAME = "redteam-freshness-candidates.md"


@dataclass
class Candidate:
    """One search result surfaced for human triage. Never auto-corroborated, never acted on."""

    query: str
    title: str
    url: str
    snippet: str


def gather_candidates(
    queries: tuple[str, ...] = DEFAULT_QUERIES, *, k_per_query: int = 3
) -> list[Candidate]:
    """Run each query in ``queries`` and return the raw results as candidates.

    Lazily imports :mod:`agentteams.research.search` so this module — and the base
    ``agentteams`` install — never gains a hard dependency on the ``research`` extra.
    Degrades to an empty list (with the caller expected to report why) rather than
    raising, matching ``research.search``'s own "any failure returns an empty result"
    contract.

    Args:
        queries: Search queries to run. Small and fixed by default — see module docstring.
        k_per_query: Max results per query.

    Returns:
        Candidates in query order; empty if the ``research`` extra isn't installed or
        every query failed.
    """
    try:
        from agentteams.research.search import web_search
    except ImportError:
        return []

    candidates: list[Candidate] = []
    for query in queries:
        for source in web_search(query, k=k_per_query):
            candidates.append(
                Candidate(query=query, title=source.title, url=source.url, snippet=source.snippet)
            )
    return candidates


def render_candidates_report(candidates: list[Candidate], *, generated_at: str | None = None) -> str:
    """Render candidates as a reviewable Markdown document.

    Args:
        candidates: Result of :func:`gather_candidates`.
        generated_at: ISO-8601 timestamp; defaults to now (UTC) when omitted.

    Returns:
        Markdown text — never a decision, never a probe, just what was found and where.
    """
    stamp = generated_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    lines = [
        "# Redteam Corpus Freshness Candidates",
        "",
        f"Generated at: `{stamp}`",
        "",
        "Raw search results for human triage — nothing here has been corroborated, "
        "nothing here has been added to the probe corpus. Read the source before acting "
        "on anything below. Run via `--redteam-freshness-check`; not part of the "
        "standing weekly audit or any automated cycle phase.",
        "",
    ]
    if not candidates:
        lines.append(
            "No candidates: either the `agentteams[research]` extra is not installed, "
            "or every query returned nothing this run."
        )
        return "\n".join(lines) + "\n"

    by_query: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_query.setdefault(c.query, []).append(c)
    for query, items in by_query.items():
        lines.append(f"## {query}")
        lines.append("")
        for item in items:
            lines.append(f"- [{item.title}]({item.url}) — {item.snippet}")
        lines.append("")
    return "\n".join(lines)


def run_freshness_check(output_dir: Path, *, dry_run: bool = False) -> Path:
    """Gather candidates, render the report, and write it under ``output_dir/references/``.

    Args:
        output_dir: The project root the report's references directory lives under.
        dry_run: When true, render but do not write.

    Returns:
        The path the report was (or would be) written to.
    """
    candidates = gather_candidates()
    text = render_candidates_report(candidates)
    path = output_dir / "references" / CANDIDATES_NAME
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return path
