"""Does lexical retrieval actually miss paraphrased queries? Measured, not assumed.

**Why this exists.** The 2026-07-30 retrieval review deferred a dense/hybrid retrieval tier
"until there is evidence lexical scoring rather than source coverage is the binding constraint."
That sentence had *no evidence behind it in either direction* and named no way to obtain any. A
deferral with no falsification criterion is indistinguishable from one that never ends.

This file supplies the criterion. It reuses the corpus and index of
``tests/test_memory_index_relevance.py`` and asks the one question that decides the matter: when
a user describes what they want **without using the target document's own vocabulary**, does BM25
still find it?

**Pre-registered before the first run** (so the result could not be fitted to a preferred answer):

- *Construction rule.* Each paraphrase targets the same document as the corresponding
  keyword query in ``test_memory_index_relevance.EVAL_PAIRS``, restated as a plain-language
  request that deliberately avoids that document's distinctive content words. No query was
  revised after seeing its score.
- *Hypothesis.* Lexical retrieval degrades substantially on this set relative to the keyword set
  (which scores 10/10).
- *Decision rule.* A large gap means the deferral has an expiry condition and this file is the
  benchmark a dense tier must beat. A small gap means the deferral is vindicated and dense
  retrieval should stay unbuilt.

**This test records; it does not fail on the finding.** It asserts a *floor* at the measured
value so a genuine regression is caught, and reports the current numbers in its failure message.
A test that went red for documenting a known limitation would be deleted or xfail-ed within a
week, and the measurement would be lost with it.

**No dense retrieval is implemented here.** Building it inside the file whose job is to decide
whether to build it would defeat the purpose — and it would need a model dependency the
stdlib-only base install forbids.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_memory_index_relevance import (  # reuses corpus + skip discipline
    EVAL_PAIRS,
    _build_corpus_index,
    _has_full_calibration_corpus,
)

pytestmark = pytest.mark.skipif(
    not _has_full_calibration_corpus(),
    reason="Full calibration corpus not present (shares test_memory_index_relevance's corpus).",
)

# ---------------------------------------------------------------------------
# The paraphrase set. Same 10 target documents as EVAL_PAIRS, same order.
# Each query is what someone would plausibly type who knows what they WANT but not what the
# document CALLS it — the class of query lexical scoring is theorised to miss.
# ---------------------------------------------------------------------------
PARAPHRASE_PAIRS: list[tuple[str, str]] = [
    ("what options can I pass on the command line when running this",
     "cli-reference.md"),
    ("how do I set this up on my machine the first time",
     "getting-started.md"),
    ("protecting the system against attackers and making it safer to run",
     "security-hardening-guide.md"),
    ("writing a new blueprint file with blanks that get filled in later",
     "template-authoring.md"),
    ("running checks automatically whenever somebody submits code",
     "bridge-ci-automation-guide.md"),
    ("making different assistant platforms work with one another",
     "interoperability.md"),
    ("keeping several separate code bases consistent with each other",
     "cross-repository-coordination-guide.md"),
    ("refreshing an existing setup after things have changed underneath it",
     "update-lifecycle-guide.md"),
    ("automatically adding extra detail about third party libraries",
     "enrichment-pipeline-guide.md"),
    ("moving from the older layout to the current one and undoing it if needed",
     "migration-guide.md"),
]

# ---------------------------------------------------------------------------
# RESULT, measured 2026-07-30 on this corpus:
#
#     keyword    top-1 = 10/10     top-3 = 10/10
#     paraphrase top-1 =  1/10     top-3 =  3/10
#
# The pre-registered hypothesis is confirmed, and by a wider margin than expected. Same corpus,
# same target documents, same retriever — only the wording changed, and top-1 recall collapsed
# from 10 to 1. Nine of ten paraphrased queries do not surface their target document at all in
# the top 3.
#
# The single top-1 hit (cli-reference.md) is not really an exception: "command line" happens to
# be that document's own vocabulary, so the query was never a true paraphrase of it.
#
# CONSEQUENCE FOR THE DEFERRAL. The review's "defer until there is evidence lexical scoring is
# the binding constraint" now has its evidence, and it points the other way from the deferral:
# for vocabulary-mismatched queries the bundled indexes are close to useless. That does NOT make
# a dense tier automatically correct — it still needs a model dependency the stdlib-only base
# install forbids, and BM25 remains excellent (10/10) when the caller knows the right words. What
# it does is convert an open-ended deferral into a decision with a stated cost and a benchmark
# any replacement must beat. Recorded in §5 Tier 3 of
# references/plans/external-retrieval-expansion-2026-07-30.report.md.
# ---------------------------------------------------------------------------

#: FLOORS recording observed behaviour, not targets. Change only alongside a note saying what
#: changed and why.
_MEASURED_PARAPHRASE_TOP1 = 1
_MEASURED_PARAPHRASE_TOP3 = 3

#: The keyword set scores 10/10 top-1 on the same corpus (test_memory_index_relevance).
_KEYWORD_TOP1 = 10


@pytest.fixture(scope="module")
def corpus_index():
    return _build_corpus_index()


def _score(index, pairs: list[tuple[str, str]], k: int) -> tuple[int, list[str]]:
    """Count how many queries rank their target document in the top ``k``.

    Args:
        index: A built memory index.
        pairs: ``(query, expected-filename-substring)`` pairs.
        k: Rank cutoff.

    Returns:
        ``(hits, misses)`` where ``misses`` are human-readable lines naming what came back.
    """
    from agentteams.memory_index import query_index

    hits = 0
    misses: list[str] = []
    for query, expected in pairs:
        results = query_index(index, query, k=k)
        names = [Path(r["path"]).name for r in results]
        if any(expected in name for name in names):
            hits += 1
        else:
            misses.append(f"  MISS want={expected!r} got={names[:k] or ['NO_RESULTS']} q={query!r}")
    return hits, misses


def test_paraphrase_recall_has_not_regressed(corpus_index):
    """Top-1 recall on paraphrased queries must not fall below the recorded measurement."""
    hits, misses = _score(corpus_index, PARAPHRASE_PAIRS, k=1)
    assert hits >= _MEASURED_PARAPHRASE_TOP1, (
        f"Paraphrase top-1 recall fell to {hits}/{len(PARAPHRASE_PAIRS)}, below the recorded "
        f"floor of {_MEASURED_PARAPHRASE_TOP1}.\n" + "\n".join(misses)
    )


def test_paraphrase_top3_recall_has_not_regressed(corpus_index):
    """Top-3 is the rank that matters in practice — @navigator opens more than one hit."""
    hits, misses = _score(corpus_index, PARAPHRASE_PAIRS, k=3)
    assert hits >= _MEASURED_PARAPHRASE_TOP3, (
        f"Paraphrase top-3 recall fell to {hits}/{len(PARAPHRASE_PAIRS)}, below the recorded "
        f"floor of {_MEASURED_PARAPHRASE_TOP3}.\n" + "\n".join(misses)
    )


def test_the_vocabulary_gap_is_real_and_large(corpus_index):
    """The finding itself, asserted so it cannot quietly stop being true.

    Same corpus, same target documents, same retriever — only the wording changes. If this ever
    fails because paraphrase recall caught up with keyword recall, the deferral of a dense tier
    has become permanent and the report's Tier 3 should say so.
    """
    paraphrase_hits, _ = _score(corpus_index, PARAPHRASE_PAIRS, k=1)
    assert paraphrase_hits < _KEYWORD_TOP1, (
        f"Paraphrase recall ({paraphrase_hits}/10) has reached keyword recall "
        f"({_KEYWORD_TOP1}/10). The lexical-vocabulary gap this benchmark exists to measure is "
        f"gone — update references/plans/external-retrieval-expansion-2026-07-30.report.md §5 "
        f"Tier 3 rather than deleting this test."
    )


def test_paraphrase_set_covers_the_same_targets_as_the_keyword_set():
    """Guards the comparison itself: a paired benchmark is only meaningful if the pairs match."""
    assert [t for _, t in PARAPHRASE_PAIRS] == [t for _, t in EVAL_PAIRS]


def test_paraphrases_avoid_the_target_filename_vocabulary():
    """Enforces the pre-registered construction rule.

    A paraphrase that reuses the document's own naming defeats the purpose — it would measure
    keyword matching while claiming to measure paraphrase. Filename stems are the most direct
    proxy for a document's distinctive vocabulary, so no query may contain one.
    """
    leaks: list[str] = []
    for query, target in PARAPHRASE_PAIRS:
        stem_words = [w for w in target.replace(".md", "").split("-") if len(w) > 4]
        for word in stem_words:
            if word in query.lower():
                leaks.append(f"{target}: query contains {word!r} — {query!r}")
    assert not leaks, "paraphrase queries leaked target vocabulary:\n" + "\n".join(leaks)
