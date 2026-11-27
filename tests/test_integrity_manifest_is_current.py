"""1.3.2: a stale integrity manifest fails the suite, instead of only printing a warning.

Before this, changing a pinned enforcement module without re-recording the manifest printed
``[INTEGRITY] … is stale`` and every test still passed, so a weakened gate could reach CI green (IM open point 4).
Re-record deliberately with ``agentteams --write-integrity-manifest`` after an intended change to a control.
"""

from __future__ import annotations

from pathlib import Path

from agentteams import integrity

_ROOT = Path(__file__).resolve().parents[1]


def test_the_repository_matches_its_integrity_manifest():
    findings = integrity.verify(_ROOT)
    assert not findings, [str(f) for f in findings]
