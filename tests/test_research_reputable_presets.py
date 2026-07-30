"""Tests for the archetype allowlist presets in agentteams.research.reputable.

The presets exist because DEFAULT_CONFIG — four general-interest domains with no primary
repositories — reduced `reputable_sources()` to "one general search filtered to Wikipedia and
three wire services", which is not usable for technical or scholarly work.

The binding constraint these tests protect is BACK-COMPATIBILITY: DEFAULT_CONFIG is the default
argument of ReputableSourceAllowlist.__init__, so any change to its contents silently changes
behaviour for every existing caller.
"""

from __future__ import annotations

import pytest

from agentteams.research.reputable import (
    DATA_CONFIG,
    DEFAULT_CONFIG,
    RESEARCH_CONFIG,
    SOFTWARE_CONFIG,
    VALID_TYPES,
    AllowlistConfig,
    ReputableSourceAllowlist,
    config_for_project_type,
)

_PRESETS = {
    "software": SOFTWARE_CONFIG,
    "research": RESEARCH_CONFIG,
    "data": DATA_CONFIG,
}


def test_default_config_contents_are_unchanged():
    """Back-compat guard. DEFAULT_CONFIG is a default argument; changing it changes callers."""
    assert DEFAULT_CONFIG.tier_by_domain == {
        "wikipedia.org": "reference",
        "reuters.com": "authoritative",
        "apnews.com": "authoritative",
        "bbc.com": "authoritative",
    }
    assert DEFAULT_CONFIG.default_repos == ()
    assert DEFAULT_CONFIG.topic_primary_repos == ()


def test_allowlist_still_defaults_to_default_config():
    assert ReputableSourceAllowlist()._config is DEFAULT_CONFIG


@pytest.mark.parametrize("project_type,expected", [
    ("software", SOFTWARE_CONFIG),
    ("documentation", SOFTWARE_CONFIG),
    ("research", RESEARCH_CONFIG),
    ("writing", RESEARCH_CONFIG),
    ("data-pipeline", DATA_CONFIG),
])
def test_known_project_types_route_to_their_preset(project_type, expected):
    assert config_for_project_type(project_type) is expected


@pytest.mark.parametrize("project_type", ["mixed", "unknown", "", "   ", "nonsense", None])
def test_unknown_project_types_fall_back_to_default(project_type):
    """An unrecognised type means 'no better information', not an error."""
    assert config_for_project_type(project_type) is DEFAULT_CONFIG


def test_project_type_matching_is_case_and_whitespace_insensitive():
    assert config_for_project_type("  SOFTWARE  ") is SOFTWARE_CONFIG


@pytest.mark.parametrize("name,config", _PRESETS.items())
def test_presets_are_materially_larger_than_the_stub(name, config):
    assert len(config.tier_by_domain) >= 9, f"{name} preset is too thin to be useful"
    assert config.default_repos, f"{name} preset has no fallback repositories"
    assert config.topic_primary_repos, f"{name} preset has no topic routing"


@pytest.mark.parametrize("name,config", _PRESETS.items())
def test_every_preset_domain_has_a_declared_type(name, config):
    """`type_by_domain` silently resolves to 'unclassified'; a shipped preset should not rely
    on that fallback, or the news-vs-encyclopedia distinction quietly stops working."""
    missing = set(config.tier_by_domain) - set(config.type_by_domain)
    assert not missing, f"{name} preset domains missing a type: {sorted(missing)}"


@pytest.mark.parametrize("name,config", _PRESETS.items())
def test_preset_types_are_from_the_declared_vocabulary(name, config):
    invalid = {t for t in config.type_by_domain.values() if t not in VALID_TYPES}
    assert not invalid, f"{name} preset uses undeclared types: {sorted(invalid)}"


@pytest.mark.parametrize("name,config", _PRESETS.items())
def test_preset_tiers_are_rankable(name, config):
    """A tier absent from tier_rank sorts to 99 — last — which would silently bury a source."""
    unrankable = {t for t in config.tier_by_domain.values() if t not in config.tier_rank}
    assert not unrankable, f"{name} preset uses unrankable tiers: {sorted(unrankable)}"


@pytest.mark.parametrize("name,config", _PRESETS.items())
def test_preset_repos_are_all_allowlisted(name, config):
    """A `site:` search against a domain that is not on the allowlist can only ever return
    hits that are then discarded — wasted requests against the endpoints whose rate limits
    this package is already working around."""
    listed = set(config.tier_by_domain)
    referenced = set(config.default_repos)
    for _, repos in config.topic_primary_repos:
        referenced.update(repos)
    orphans = {r for r in referenced if not any(r == d or r.endswith("." + d) for d in listed)}
    assert not orphans, f"{name} preset routes to non-allowlisted domains: {sorted(orphans)}"


@pytest.mark.parametrize("name,config", _PRESETS.items())
def test_presets_are_frozen_config_objects(name, config):
    assert isinstance(config, AllowlistConfig)
    with pytest.raises((AttributeError, TypeError)):
        config.tier_by_domain = {}  # type: ignore[misc]


def test_tier_of_resolves_through_a_preset():
    allowlist = ReputableSourceAllowlist(SOFTWARE_CONFIG)
    assert allowlist.tier_of("https://docs.python.org/3/library/re.html") == "authoritative"
    assert allowlist.tier_of("https://random.example.com/x") is None
