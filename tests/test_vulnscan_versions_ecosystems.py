"""``versions.version_key`` orderings of Go, RubyGems, Maven and NuGet versions (vlc N7): lenient, never raises."""

from __future__ import annotations

import pytest

from agentteams.vulnscan.versions import version_key

@pytest.mark.parametrize(("low", "high"), [
    ("0.0.0-20240101000000-abcdef123456", "0.0.0-20240202000000-000000000000"),   # Go pseudo: by timestamp
    ("0.0.0-20240101000000-abcdef123456", "0.0.1"),
    ("1.2.3", "1.2.4-0.20240101000000-abcdef123456"),
    ("1.2.4-0.20240101000000-abcdef123456", "1.2.4-rc.1"),                         # pseudo before any pre-release
    ("1.2.4-0.20240101000000-abcdef123456", "1.2.4"),
    ("1.2.3-pre", "1.2.3-pre.0.20240101000000-abcdef123456"),
    ("1.0.0.pre", "1.0.0"), ("1.0.0.beta2", "1.0.0.rc1"), ("1.0.0.rc1", "1.0.0"),  # RubyGems
    ("1.0-SNAPSHOT", "1.0"), ("1.0-rc1", "1.0-SNAPSHOT"), ("1.0-M1", "1.0-rc1"), ("1.0-beta", "1.0-M1"),  # Maven
    ("5.3.17.RELEASE", "5.3.18"), ("1.0", "1.0-SP1"), ("31.1-jre", "32.0.0-jre"),
    ("1.2.3.4", "1.2.3.10"), ("4.3.0", "4.3.0.1"), ("13.0.1-beta1", "13.0.1"),      # NuGet
])
def test_version_key_orders_go_gem_maven_and_nuget_versions(low: str, high: str) -> None:
    assert version_key(low) < version_key(high)


@pytest.mark.parametrize(("a", "b"), [("5.3.18.RELEASE", "5.3.18"), ("5.3.18.Final", "5.3.18"),
                                      ("1.0.GA", "1.0"), ("4.3.0", "4.3.0.0"), ("v1.2.3", "1.2.3")])
def test_version_key_treats_final_qualifiers_as_the_release(a: str, b: str) -> None:
    assert version_key(a) == version_key(b)


@pytest.mark.parametrize("text", ["", "v", "-", "0.0.0-99999999999999-zzzz", "1.0-" + "9" * 5000, "²", "1..2", "sp"])
def test_version_key_never_raises(text: str) -> None:
    version_key(text)
