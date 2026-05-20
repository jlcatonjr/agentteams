"""Relevance eval harness for the memory index (I4).

Builds a real BM25 index over the AgentTeams N≈105 source corpus and asserts
that 10 carefully-chosen query / expected-document pairs achieve top-1 accuracy
of ≥ 9/10.

Design principles:
- Queries are grounded in unique phrasing drawn from the target documents so
  they survive normal text evolution without frequent fixup.
- The grid-search over K1 ∈ {1.2, 1.5, 2.0} × B ∈ {0.75, 0.85, 1.0} was
  run on 2026-05-19 and all 9 combinations achieved 10/10 — parameters are
  non-differentiating at this corpus scale.  The harness asserts ≥ 9/10 so
  minor corpus drift does not cause a spurious failure.
- The test is skipped when the corpus is absent (CI environments without the
  full docs_src / references tree).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

# Require docs_src and references to be present; skip otherwise.
_DOCS_SRC = REPO_ROOT / "docs_src"
_REFERENCES = REPO_ROOT / "references"
pytestmark = pytest.mark.skipif(
    not _DOCS_SRC.is_dir() or not _REFERENCES.is_dir(),
    reason="Full docs_src / references corpus not present",
)

# ---------------------------------------------------------------------------
# Eval set — 10 (query, expected-filename-substring) pairs.
# Each query contains terminology specific enough to the target document that
# it reliably outranks the other ~104 corpus documents.
# ---------------------------------------------------------------------------
EVAL_PAIRS: list[tuple[str, str]] = [
    (
        "refresh-index query-index synopsis CLI flags",
        "cli-reference.md",
    ),
    (
        "PyPI not yet published install clone virtual",
        "getting-started.md",
    ),
    (
        "OWASP Top Ten vulnerability hardening agent generation",
        "security-hardening-guide.md",
    ),
    (
        "YAML front matter template authoring model tools",
        "template-authoring.md",
    ),
    (
        "GitHub Actions trigger on push pull_request CI bridge",
        "bridge-ci-automation-guide.md",
    ),
    (
        "convert-from interop-from bridge-from mode canonical",
        "interoperability.md",
    ),
    (
        "repo-liaison adjacent orchestrator registry maintenance",
        "cross-repository-coordination-guide.md",
    ),
    (
        "update lifecycle merge overwrite backup stale refresh",
        "update-lifecycle-guide.md",
    ),
    (
        "enrich tool enrichment catalog pipeline agentteams",
        "enrichment-pipeline-guide.md",
    ),
    (
        "migrate flag legacy snapshot tag overwrite revert-migration rollback",
        "migration-guide.md",
    ),
]

_MIN_ACCURACY = 9  # out of 10


def _build_corpus_index():
    """Build index over the full AgentTeams corpus (mirrors build_team._memory_index_sources)."""
    from agentteams.memory_index import build_memory_index

    sources: list[Path] = []
    for name in ("CHANGELOG.md", "README.md", "build-team-plan.md"):
        p = REPO_ROOT / name
        if p.exists():
            sources.append(p)
    sources.extend(sorted((REPO_ROOT / "workSummaries").rglob("*.md")))
    sources.extend(sorted(_DOCS_SRC.glob("*.md")))
    sources.extend(sorted(_REFERENCES.rglob("*.md")))
    return build_memory_index(sources)


@pytest.fixture(scope="module")
def corpus_index():
    return _build_corpus_index()


# ---------------------------------------------------------------------------
# Top-1 accuracy — asserted as a batch to give the full failure list on miss.
# ---------------------------------------------------------------------------

def test_top1_accuracy(corpus_index):
    """At least _MIN_ACCURACY of the 10 eval queries must hit top-1."""
    from agentteams.memory_index import query_index

    misses: list[str] = []
    for query, expected in EVAL_PAIRS:
        results = query_index(corpus_index, query, k=3)
        top_name = Path(results[0]["path"]).name if results else "NO_RESULTS"
        if expected not in top_name:
            misses.append(f"MISS expected={expected!r} top={top_name!r} q={query!r}")

    correct = len(EVAL_PAIRS) - len(misses)
    assert correct >= _MIN_ACCURACY, (
        f"Top-1 accuracy {correct}/{len(EVAL_PAIRS)} < {_MIN_ACCURACY}/10.\n"
        + "\n".join(misses)
    )


# ---------------------------------------------------------------------------
# Grid search — documents that all tested params are in the ≥ _MIN_ACCURACY tier.
# This test is informational; it validates that no regression has narrowed the
# non-differentiating region.
# ---------------------------------------------------------------------------

def test_grid_search_all_params_above_threshold(corpus_index):
    """All 9 BM25 param combos must achieve ≥ _MIN_ACCURACY top-1 accuracy."""
    import agentteams.memory_index as mi

    saved_k1, saved_b = mi._K1, mi._B
    try:
        failures: list[str] = []
        for k1 in (1.2, 1.5, 2.0):
            for b in (0.75, 0.85, 1.0):
                mi._K1 = k1
                mi._B = b
                correct = sum(
                    1
                    for q, e in EVAL_PAIRS
                    if (r := mi.query_index(corpus_index, q, k=3))
                    and e in Path(r[0]["path"]).name
                )
                if correct < _MIN_ACCURACY:
                    failures.append(f"K1={k1}, B={b}: {correct}/{len(EVAL_PAIRS)}")
        assert not failures, "Some param combos fell below threshold:\n" + "\n".join(failures)
    finally:
        mi._K1, mi._B = saved_k1, saved_b


# ---------------------------------------------------------------------------
# Top-3 accuracy — each eval query must rank the target document in the top 3.
# This is a harder regression guard than top-1 on edge-case queries.
# ---------------------------------------------------------------------------

def test_top3_accuracy_perfect(corpus_index):
    """All 10 eval queries must find the expected document within top-3."""
    from agentteams.memory_index import query_index

    misses: list[str] = []
    for query, expected in EVAL_PAIRS:
        results = query_index(corpus_index, query, k=3)
        names = [Path(r["path"]).name for r in results]
        if not any(expected in n for n in names):
            misses.append(f"NOT IN TOP-3: expected={expected!r} top3={names!r}")
    assert not misses, "Some targets missing from top-3:\n" + "\n".join(misses)
