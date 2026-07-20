"""Tests for agentteams.research.reputable — exercised against an INJECTED test config, never
the shipped DEFAULT_CONFIG, per this plan's Acceptance criteria."""

from __future__ import annotations

from agentteams.research.reputable import (
    AllowlistConfig,
    ReputableSourceAllowlist,
)
from agentteams.research.search import Source

TEST_CONFIG = AllowlistConfig(
    tier_by_domain={
        "example.org": "authoritative",
        "sub.example.org": "primary",
        "wiki.test": "reference",
        "scoped.test": "authoritative",
    },
    type_by_domain={
        "example.org": "government",
        "sub.example.org": "primary-text",
        "wiki.test": "encyclopedia",
    },
    topic_primary_repos=(
        (("science", "physics"), ("example.org",)),
    ),
    path_scope={"scoped.test": "/articles/"},
    default_repos=("wiki.test",),
)


def _allowlist() -> ReputableSourceAllowlist:
    return ReputableSourceAllowlist(TEST_CONFIG)


def test_registered_domain_prefers_longest_match() -> None:
    """The exact bug class this pattern's LingoFriend origin caught live: a subdomain that is
    also a suffix of an unrelated, shorter, already-listed parent domain must resolve to itself,
    not to the parent, regardless of dict insertion order."""
    al = _allowlist()
    assert al._registered_domain("sub.example.org") == "sub.example.org"
    assert al._registered_domain("example.org") == "example.org"
    assert al._registered_domain("www.example.org") == "example.org"
    assert al._registered_domain("unrelated.test") is None


def test_to_reputable_tags_tier_and_type() -> None:
    al = _allowlist()
    hit = Source(title="t", url="https://example.org/page", snippet="s")
    rep = al._to_reputable(hit)
    assert rep is not None
    assert rep.tier == "authoritative"
    assert rep.type == "government"


def test_to_reputable_defaults_type_to_unclassified_when_absent() -> None:
    al = _allowlist()
    hit = Source(title="t", url="https://scoped.test/articles/x", snippet="s")
    rep = al._to_reputable(hit)
    assert rep is not None
    assert rep.type == "unclassified"


def test_to_reputable_refuses_unlisted_domain() -> None:
    al = _allowlist()
    hit = Source(title="t", url="https://not-listed.test/page", snippet="s")
    assert al._to_reputable(hit) is None


def test_path_scope_keeps_in_scope_refuses_out_of_scope() -> None:
    al = _allowlist()
    in_scope = Source(title="t", url="https://scoped.test/articles/x", snippet="s")
    out_of_scope = Source(title="t", url="https://scoped.test/other/x", snippet="s")
    assert al._to_reputable(in_scope) is not None
    assert al._to_reputable(out_of_scope) is None


def test_primary_repos_for_routes_on_topic_keyword() -> None:
    al = _allowlist()
    repos, is_fallback = al._primary_repos_for("a question about physics")
    assert repos == ("example.org",)
    assert is_fallback is False


def test_primary_repos_for_falls_back_to_default() -> None:
    al = _allowlist()
    repos, is_fallback = al._primary_repos_for("something unrelated entirely")
    assert repos == ("wiki.test",)
    assert is_fallback is True


def test_tier_of_public_helper() -> None:
    al = _allowlist()
    assert al.tier_of("https://example.org/x") == "authoritative"
    assert al.tier_of("https://not-listed.test/x") is None


def test_default_config_is_internally_consistent() -> None:
    """The shipped DEFAULT_CONFIG (small, generic, non-LingoFriend-specific per Constraints) must
    still satisfy the same basic invariants any config must: every tier_by_domain key has a
    type_by_domain entry or gracefully defaults, and tier_rank covers every tier value used."""
    from agentteams.research.reputable import DEFAULT_CONFIG

    for domain, tier in DEFAULT_CONFIG.tier_by_domain.items():
        assert tier in DEFAULT_CONFIG.tier_rank, f"{domain}'s tier {tier!r} has no rank"
    valid_types = {"news", "academic", "government", "encyclopedia", "primary-text", "book"}
    for domain, type_ in DEFAULT_CONFIG.type_by_domain.items():
        assert type_ in valid_types, f"{domain}'s type {type_!r} is not in the fixed vocabulary"
