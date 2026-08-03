"""Performance-oriented regression checks for memory-index retrieval.

These tests use conservative thresholds to catch severe regressions while
remaining stable across CI environments.
"""

from __future__ import annotations

import time
from pathlib import Path

from agentteams.memory_index import build_memory_index, query_index


def _seed_corpus(tmp_path: Path, *, n_docs: int) -> list[Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(n_docs):
        p = tmp_path / f"doc-{i:04d}.md"
        topic = "drift detection baseline" if i % 3 == 0 else "handoff schema validation"
        p.write_text(
            "# Doc\n\n"
            f"This document {i} covers {topic} and audit pipeline decisions.\n\n"
            "Additional context about update lifecycle and memory index behavior.\n",
            encoding="utf-8",
        )
        paths.append(p)
    return paths


def test_build_index_work_per_document_is_constant(tmp_path):
    """Doubling the corpus must not explode the work — asserted structurally, not on a clock.

    This was a wall-clock ratio, `large_s < small_s * 4.0 + 0.05`, and it flaked on
    macOS/3.11 during PR #85 while three sibling jobs passed the same commit; a re-run of that
    identical commit passed. The 60-document baseline had measured **3.3 ms**, so the
    multiplicative term contributed ~13 ms and the whole assertion rested on a 50 ms budget on
    a shared runner. Nothing was slow. A ratio against a near-zero denominator measures
    scheduler noise, and a red CI that is not a defect teaches people to re-run instead of read.

    The claim is structural, so measure structure. Postings written per document is exactly
    constant across corpus sizes and identical on every machine.

    **What this does and does not establish.** It is a statement about *this corpus*: the seeded
    documents share a small vocabulary, so a constant posting count per document is a property
    of the fixture as much as of the indexer. It would catch a build that started writing
    quadratically many postings. It is not a proof that `build_memory_index` is O(n).
    """
    sizes = (60, 120, 240)
    per_doc = {}
    for n in sizes:
        index = build_memory_index(_seed_corpus(tmp_path / f"c{n}", n_docs=n))
        entries = sum(len(v) for v in index["postings"].values())
        assert len(index["documents"]) == n
        per_doc[n] = entries / n

    assert len(set(per_doc.values())) == 1, (
        f"postings written per document is no longer constant across corpus sizes: {per_doc}. "
        "Work is growing superlinearly in the corpus."
    )
    assert per_doc[sizes[0]] > 0, "no postings written — the index build regressed to a no-op"


def test_build_index_is_not_pathologically_slow(tmp_path):
    """The floor a structural metric cannot provide: an indexer 100x slower per document.

    Deliberately an ABSOLUTE ceiling on a seconds scale, not a ratio against another
    measurement. The ratio form is what flaked; the margin here is ~1000x over the observed
    time, so it fails only on a real regression rather than on a busy runner.
    """
    paths = _seed_corpus(tmp_path / "speed", n_docs=240)

    start = time.perf_counter()
    build_memory_index(paths)
    elapsed = time.perf_counter() - start

    assert elapsed < 10.0, f"indexing 240 small documents took {elapsed:.2f}s"


def test_query_latency_bound_for_moderate_corpus(tmp_path):
    """Left as a wall-clock check, deliberately, unlike the scaling test above.

    Same family, different margin. This budgets 2.5 s for 25 queries — ~100 ms each against a
    sub-millisecond reality, a margin of roughly 100x. The assertion that flaked was a *ratio*
    against a 3.3 ms measurement, where a 15x margin evaporates under ordinary scheduler noise.
    An absolute ceiling with a large margin is a different instrument from a ratio with a small
    one, and only the second was broken.
    """
    paths = _seed_corpus(tmp_path / "query", n_docs=180)
    idx = build_memory_index(paths)

    start = time.perf_counter()
    for _ in range(25):
        hits = query_index(idx, "drift detection baseline", k=5)
        assert hits
    elapsed = time.perf_counter() - start

    # Conservative bound across local + CI environments.
    assert elapsed < 2.5
