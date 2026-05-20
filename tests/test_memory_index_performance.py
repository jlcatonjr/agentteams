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


def test_build_index_scaling_is_reasonable(tmp_path):
    small_paths = _seed_corpus(tmp_path / "small", n_docs=60)
    large_paths = _seed_corpus(tmp_path / "large", n_docs=120)

    t0 = time.perf_counter()
    build_memory_index(small_paths)
    small_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    build_memory_index(large_paths)
    large_s = time.perf_counter() - t1

    # Doubling corpus size should not explode superlinearly in this range.
    assert large_s < (small_s * 4.0 + 0.05)


def test_query_latency_bound_for_moderate_corpus(tmp_path):
    paths = _seed_corpus(tmp_path / "query", n_docs=180)
    idx = build_memory_index(paths)

    start = time.perf_counter()
    for _ in range(25):
        hits = query_index(idx, "drift detection baseline", k=5)
        assert hits
    elapsed = time.perf_counter() - start

    # Conservative bound across local + CI environments.
    assert elapsed < 2.5
