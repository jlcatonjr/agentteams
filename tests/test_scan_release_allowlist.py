"""Tests for scan.RELEASE_VALUE_ALLOWLIST (SECRET-SCANNER-HEX-FALSE-POSITIVE).

Sample values are built at run time, never written as literals, so this file
never carries a high-entropy token of its own.
"""

import base64
import hashlib
import re

import pytest

from agentteams import scan
from agentteams.scan import scan_content


def _detected(token: str) -> bool:
    """True when the scanner, with the shipped allowlist, flags *token* on a line."""
    assert token not in scan.RELEASE_VALUE_ALLOWLIST
    return scan._token_entropy(token) >= scan._ENTROPY_MIN_BITS + 0.1 and any(
        f.category == "credential" for f in scan_content(f"Release commit {token}\n")
    )


def _hex40(seed: bytes) -> str:
    """A 40-hex token, re-hashed until the scanner clearly flags it."""
    for i in range(1000):
        value = hashlib.sha1(seed + str(i).encode()).hexdigest()
        if _detected(value) and re.search(r"[a-f]", value) and re.search(r"\d", value):
            return value
    raise AssertionError("no qualifying sample")


def _fingerprint(seed: bytes) -> str:
    """A ``SHA256:<base64>`` fingerprint the scanner flags when unlisted."""
    for i in range(1000):
        body = base64.b64encode(hashlib.sha256(seed + str(i).encode()).digest()).decode().rstrip("=")
        if _detected("SHA256:" + body):
            return "SHA256:" + body
    raise AssertionError("no qualifying sample")


LISTED_COMMIT = _hex40(b"listed-commit")
LISTED_FPR = _fingerprint(b"listed-fingerprint")
UNLISTED_COMMIT = _hex40(b"unlisted-commit")
UNLISTED_FPR = _fingerprint(b"unlisted-fingerprint")


def _entropy_findings(content: str) -> list:
    return [
        f for f in scan_content(content, "release-notes.md")
        if f.category == "credential"
    ]


@pytest.fixture
def allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scan, "RELEASE_VALUE_ALLOWLIST", frozenset({LISTED_COMMIT, LISTED_FPR})
    )


def test_shipped_allowlist_is_a_frozenset_of_strings() -> None:
    assert isinstance(scan.RELEASE_VALUE_ALLOWLIST, frozenset)
    assert all(isinstance(v, str) for v in scan.RELEASE_VALUE_ALLOWLIST)


@pytest.mark.parametrize("value", [LISTED_COMMIT, LISTED_FPR])
def test_values_are_flagged_without_the_allowlist(value: str) -> None:
    # Precondition: each sample really trips the detector when not listed.
    assert _entropy_findings(f"Release commit {value}\n")


@pytest.mark.parametrize(
    "line",
    [
        "Release commit {commit}\n",
        "Signed by {fpr}\n",
        "- agentteams v1.0.0 `{commit}` signed by `{fpr}`.\n",
        "{commit}\n",
    ],
)
def test_allowlisted_values_pass(allowlist: None, line: str) -> None:
    content = line.format(commit=LISTED_COMMIT, fpr=LISTED_FPR)
    assert _entropy_findings(content) == []


def test_allowlisted_value_in_secret_context_passes(allowlist: None) -> None:
    assert _entropy_findings(f"release tag commit (not a token) {LISTED_COMMIT}\n") == []


@pytest.mark.parametrize("value", [UNLISTED_COMMIT, UNLISTED_FPR])
def test_unlisted_values_still_flagged(allowlist: None, value: str) -> None:
    assert _entropy_findings(f"Release commit {value}\n")


@pytest.mark.parametrize(
    "glued",
    [
        "x" + LISTED_COMMIT,
        LISTED_COMMIT + "0",
        LISTED_COMMIT + "-x",
        "v_" + LISTED_COMMIT,
        LISTED_FPR + "A",
        "X" + LISTED_FPR,
        "key:" + LISTED_FPR,
        LISTED_COMMIT.upper(),
        LISTED_COMMIT[:-1],
    ],
)
def test_glued_or_altered_value_still_flagged(allowlist: None, glued: str) -> None:
    assert _entropy_findings(f"Release commit {glued}\n")


@pytest.mark.parametrize(
    "marker",
    [
        "# scanner: allow",
        "<!-- scanner: allow -->",
        "# noqa: secret",
        "# agentteams-scan: ignore",
    ],
)
def test_self_exemption_comment_does_not_help(allowlist: None, marker: str) -> None:
    same_line = f"{UNLISTED_COMMIT}  {marker}\n"
    own_line = f"{marker}\nRelease commit {UNLISTED_COMMIT}\n"
    assert _entropy_findings(same_line)
    assert _entropy_findings(own_line)


def test_allowlisted_filename_gives_no_exemption(allowlist: None) -> None:
    findings = [
        f for f in scan_content(f"commit {UNLISTED_COMMIT}\n", LISTED_COMMIT + ".md")
        if f.category == "credential"
    ]
    assert findings
