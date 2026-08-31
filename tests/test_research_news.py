"""Tests for agentteams.research.news."""

from __future__ import annotations

from agentteams.research.news import (
    PerspectiveKind,
    is_news_source,
    perspective_attribution,
)
from agentteams.research.reputable import ReputableSource


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
