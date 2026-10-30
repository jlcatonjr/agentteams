"""Tests for agentteams.research.news."""

from __future__ import annotations

import httpx
import pytest

from agentteams.research.news import (
    PerspectiveKind,
    PerspectiveSet,
    gather_perspectives,
    is_news_source,
    perspective_attribution,
    perspective_summary,
)
from agentteams.research.reputable import (
    DEFAULT_CONFIG,
    ReputableSource,
    ReputableSourceAllowlist,
)
from agentteams.research.search import Source


def _news_source(**overrides) -> ReputableSource:
    defaults = dict(
        title="Headline",
        url="https://bbc.com/news/story",
        snippet="Snippet text",
        domain="bbc.com",
        tier="authoritative",
        type="news",
    )
    defaults.update(overrides)
    return ReputableSource(**defaults)


def test_is_news_source_true_for_news_type() -> None:
    assert is_news_source(_news_source()) is True


def test_is_news_source_false_for_non_news_type() -> None:
    assert is_news_source(_news_source(type="encyclopedia", domain="wikipedia.org")) is False


def test_is_news_source_false_for_unclassified_default() -> None:
    assert is_news_source(_news_source(type="unclassified")) is False


def test_perspective_attribution_with_known_date() -> None:
    result = perspective_attribution(_news_source(), "2026-07-18")
    assert "bbc.com" in result
    assert "2026-07-18" in result
    assert "reported" in result


def test_perspective_attribution_with_unknown_date_never_fabricates() -> None:
    result = perspective_attribution(_news_source(), None)
    assert "bbc.com" in result
    assert "reported" in result
    # Must not silently omit the caveat -- an absent date must read as absent, not as if
    # every digit-shaped string were scrubbed out and no one would notice the gap.
    assert "not available" in result


def test_perspective_kind_values() -> None:
    reported: PerspectiveKind = "reported"
    contested: PerspectiveKind = "contested"
    assert reported == "reported"
    assert contested == "contested"


# --- perspective coverage (R8) ---------------------------------------------------------------

CLAIM = "Minister described the harbour expansion plan as reckless"
MATCHING_SNIPPET = "The minister called the harbour expansion reckless on Tuesday."


def _stub(hits: list[Source]):
    calls: list[str] = []

    def search_fn(query: str) -> list[Source]:
        calls.append(query)
        return hits

    search_fn.calls = calls  # type: ignore[attr-defined]
    return search_fn


def _raising(exc: BaseException):
    def search_fn(query: str) -> list[Source]:
        raise exc

    return search_fn


def test_classify_tags_allowlisted_hit_and_rejects_others() -> None:
    allow = ReputableSourceAllowlist(DEFAULT_CONFIG)
    rep = allow.classify(Source("t", "https://www.reuters.com/world/x", "s"))
    assert rep is not None and rep.domain == "reuters.com" and rep.type == "news"
    assert allow.classify(Source("t", "https://example.com/x", "s")) is None


def test_contested_without_second_outlet_says_single_source() -> None:
    ps = gather_perspectives(CLAIM, _news_source(), "2026-07-18", search_fn=_stub([]))
    assert ps.coverage == "single-source"
    summary = perspective_summary(ps)
    assert "bbc.com reported (2026-07-18)" in summary
    assert "contested" in summary
    assert "single source" in summary
    assert "no second outlet found" in summary


def test_second_allowlisted_news_outlet_makes_multi_outlet() -> None:
    search = _stub([Source("Minister", "https://apnews.com/article/1", MATCHING_SNIPPET)])
    ps = gather_perspectives(CLAIM, _news_source(), "2026-07-18", search_fn=search)
    assert search.calls == [CLAIM]
    assert ps.coverage == "multi-outlet"
    assert ps.outlets == (("bbc.com", "2026-07-18"), ("apnews.com", None))
    summary = perspective_summary(ps)
    assert "also covered by apnews.com (date not available)" in summary
    assert "contested" in summary
    assert "reported by" not in summary


def test_same_domain_non_news_and_unlisted_hits_ignored() -> None:
    hits = [
        Source("t", "https://www.bbc.com/news/other", MATCHING_SNIPPET),  # same domain as first
        Source("t", "https://en.wikipedia.org/wiki/X", MATCHING_SNIPPET),  # encyclopedia
        Source("t", "https://example.com/news", MATCHING_SNIPPET),  # not allowlisted
    ]
    ps = gather_perspectives(CLAIM, _news_source(), None, search_fn=_stub(hits))
    assert ps.coverage == "single-source"
    assert ps.outlets == (("bbc.com", None),)


def test_hit_whose_snippet_lacks_claim_terms_is_ignored() -> None:
    hits = [Source("Minister", "https://reuters.com/a", "Weather forecast for the weekend.")]
    ps = gather_perspectives(CLAIM, _news_source(), None, search_fn=_stub(hits))
    assert ps.coverage == "single-source"


def test_duplicate_outlet_counted_once_and_k_caps_additions() -> None:
    hits = [
        Source("a", "https://apnews.com/1", MATCHING_SNIPPET),
        Source("b", "https://apnews.com/2", MATCHING_SNIPPET),
        Source("c", "https://reuters.com/3", MATCHING_SNIPPET),
    ]
    ps = gather_perspectives(CLAIM, _news_source(), None, search_fn=_stub(hits))
    assert [d for d, _ in ps.outlets] == ["bbc.com", "apnews.com", "reuters.com"]
    capped = gather_perspectives(CLAIM, _news_source(), None, search_fn=_stub(hits), k=1)
    assert [d for d, _ in capped.outlets] == ["bbc.com", "apnews.com"]


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), OSError("no route")]
)
def test_search_failure_leaves_single_source(exc: BaseException) -> None:
    ps = gather_perspectives(CLAIM, _news_source(), "2026-07-18", search_fn=_raising(exc))
    assert ps.coverage == "single-source"
    assert "no second outlet found" in perspective_summary(ps)


def test_unexpected_search_error_propagates() -> None:
    with pytest.raises(RuntimeError):
        gather_perspectives(CLAIM, _news_source(), None, search_fn=_raising(RuntimeError("bug")))


def test_reported_kind_is_not_searched_and_keeps_attribution_wording() -> None:
    search = _stub([Source("t", "https://apnews.com/1", MATCHING_SNIPPET)])
    ps = gather_perspectives(
        CLAIM, _news_source(), "2026-07-18", kind="reported", search_fn=search
    )
    assert search.calls == []
    assert perspective_summary(ps) == perspective_attribution(_news_source(), "2026-07-18")


def test_no_search_fn_means_no_search() -> None:
    ps = gather_perspectives(CLAIM, _news_source(), None)
    assert ps.outlets == (("bbc.com", None),)


def test_summary_rejects_empty_outlets() -> None:
    with pytest.raises(ValueError):
        perspective_summary(PerspectiveSet(claim=CLAIM, kind="contested", outlets=()))


@pytest.mark.parametrize("kind", ["opinion", "", "Contested"])
def test_unknown_kind_is_rejected(kind: str) -> None:
    with pytest.raises(ValueError, match="kind"):
        gather_perspectives(CLAIM, _news_source(), None, kind=kind)  # type: ignore[arg-type]


@pytest.mark.parametrize("k", [-1, 1.5, "3", True])
def test_invalid_k_is_rejected(k: object) -> None:
    with pytest.raises(ValueError, match="k must be"):
        gather_perspectives(CLAIM, _news_source(), None, k=k)  # type: ignore[arg-type]


def test_k_zero_searches_nothing() -> None:
    search = _stub([Source("t", "https://apnews.com/1", MATCHING_SNIPPET)])
    ps = gather_perspectives(CLAIM, _news_source(), None, search_fn=search, k=0)
    assert search.calls == [] and ps.coverage == "single-source"


def test_contested_summary_leads_with_the_attribution_wording() -> None:
    ps = PerspectiveSet(claim=CLAIM, kind="contested", outlets=(("bbc.com", "2026-07-18"),))
    assert perspective_summary(ps).startswith(perspective_attribution(_news_source(), "2026-07-18") + "; ")
