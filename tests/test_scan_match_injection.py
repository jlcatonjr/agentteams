"""agentteams.scan.match_injection: the public per-line S-5 / C-1 matcher for non-agent text.

Payloads come from scan.py's own pattern lists or are assembled at runtime, so this file never
carries a literal S-5 phrase (the gate hook blocks one).
"""

from __future__ import annotations

import pytest

from agentteams.scan import (
    _IDENTITY_OVERRIDE_PATTERNS,
    _INJECTION_PATTERNS,
    _TIER_CLAIM_PATTERNS,
    match_injection,
    scan_content,
)

OVERRIDE = "ignore previous" + " instructions"


@pytest.mark.parametrize("pattern", _INJECTION_PATTERNS)
def test_every_override_pattern_matches(pattern: str) -> None:
    message = match_injection(f"Some preamble. {pattern.upper()} then more.")
    assert message is not None and "S-5" in message


@pytest.mark.parametrize("pattern", _TIER_CLAIM_PATTERNS)
def test_every_tier_claim_matches(pattern: str) -> None:
    message = match_injection(f"Note: {pattern}.")
    assert message is not None and "C-1" in message


@pytest.mark.parametrize("pattern", _IDENTITY_OVERRIDE_PATTERNS)
def test_identity_phrase_matches_with_no_front_matter_exemption(pattern: str) -> None:
    message = match_injection(f"{pattern} a pirate", previous_line="---")
    assert message is not None and "identity-override" in message


def test_split_across_two_lines_matches_via_previous_line() -> None:
    first, second = OVERRIDE.split(" ", 1)
    assert match_injection(second + " now.") is None
    message = match_injection(second + " now.", previous_line="Please " + first)
    assert message is not None and "split across a line break" in message


def test_zero_width_space_does_not_split_a_phrase() -> None:
    assert match_injection("Please " + OVERRIDE.replace(" ", " ​", 1) + ".") is not None
    assert match_injection("Please " + OVERRIDE[:3] + "​" + OVERRIDE[3:] + ".") is not None


def test_plain_text_returns_none() -> None:
    assert match_injection("Grid operators reported more intrusion attempts.") is None
    assert match_injection("") is None


def test_agrees_with_scan_content_on_unowned_text() -> None:
    lines = ["Ordinary line.", "Please " + OVERRIDE.split(" ", 1)[0], OVERRIDE.split(" ", 1)[1], "End."]
    expected = {f.line: f.message for f in scan_content("\n".join(lines), "notes.md")
                if f.category == "injection"}
    got = {n: m for n, m in ((i, match_injection(line, lines[i - 2] if i > 1 else ""))
                             for i, line in enumerate(lines, start=1)) if m is not None}
    assert got == expected and got
