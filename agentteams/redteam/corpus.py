"""corpus.py — the judgment-layer payload corpus, and the assertion that keeps it honest.

The deterministic scanner (:mod:`agentteams.scan`) catches literal instruction overrides. It
says so itself, and the limit is accepted rather than hidden. What it cannot catch is the
semantic case — a payload that never uses a trigger phrase but still asks an agent to act
against its constitution. Measuring *that* needs a live agent, which is why the corpus exists.

The corpus carries a ``scanner_matches`` claim per payload, and this module **verifies the
claim against the scanner** rather than trusting it. A corpus the scanner already catches
would re-measure the mechanical layer and say nothing about the judgment layer — a coverage
claim with the wrong denominator, which is the failure mode this whole audit is built around.

The corpus is loaded and verified daily. It is **not run against live subagents** by the daily
audit: doing so spawns agents and costs tokens, and the handoff requires explicit operator
authorization first. The daily report therefore states the corpus as an *unmeasured
population* rather than omitting it, because a coverage number that quietly excludes what it
did not look at is exactly the defect being guarded against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: Where the corpus lives, relative to the repository root. Kept in ``tests/`` because the
#: live harness that consumes it (``tests/redteam/run_harness.py``) is operator-driven and
#: test-adjacent; this module reads it as data, not as a test import.
CORPUS_REL_PATH = "tests/redteam/payloads.json"


@dataclass(frozen=True)
class CorpusVerification:
    """The result of checking every payload's ``scanner_matches`` claim.

    Attributes:
        total: Payloads in the corpus.
        outside_scanner: Payloads the deterministic layer does not catch — the judgment-layer
            population.
        inside_scanner: Payloads the deterministic layer does catch, which prove the check
            itself works in both directions.
        mismatches: Payloads whose claim disagrees with what the scanner actually does. Any
            entry here means the corpus is describing a scanner that no longer exists.
        classes: Distinct payload classes present.
    """

    total: int
    outside_scanner: int
    inside_scanner: int
    mismatches: list[str]
    classes: tuple[str, ...]

    @property
    def is_sound(self) -> bool:
        """True when every claim holds and both directions are exercised."""
        return not self.mismatches and self.outside_scanner > 0 and self.inside_scanner > 0


def load_corpus(root: Path) -> list[dict]:
    """Load the judgment-layer payload corpus.

    Args:
        root: Repository root.

    Returns:
        The payload dicts, or ``[]`` when the corpus is absent — a generated team that has not
        authored one is unconfigured, not broken.
    """
    path = root / CORPUS_REL_PATH
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["payloads"]


def verify_corpus(payloads: list[dict]) -> CorpusVerification:
    """Check each payload's ``scanner_matches`` claim against :mod:`agentteams.scan`.

    Args:
        payloads: Payload dicts from :func:`load_corpus`.

    Returns:
        A :class:`CorpusVerification`. Callers treat a non-sound result as *harness broken*
        rather than as a finding: it means the instrument is mis-calibrated, and a
        mis-calibrated instrument reporting "no findings" is the worst available outcome.
    """
    from agentteams.scan import scan_content, verdict_for_findings

    mismatches: list[str] = []
    outside = 0
    inside = 0
    for payload in payloads:
        findings = scan_content(payload["content"], filename="reviewed/untrusted.md")
        caught = verdict_for_findings(findings) == "HALT"
        if caught:
            inside += 1
        else:
            outside += 1
        if caught != bool(payload.get("scanner_matches")):
            mismatches.append(
                f"{payload['id']}: corpus claims scanner_matches="
                f"{payload.get('scanner_matches')}, scanner returned "
                f"{'HALT' if caught else 'no HALT'}"
            )
    classes = tuple(sorted({str(p.get("class", "")) for p in payloads if p.get("class")}))
    return CorpusVerification(
        total=len(payloads),
        outside_scanner=outside,
        inside_scanner=inside,
        mismatches=mismatches,
        classes=classes,
    )


__all__ = ["CORPUS_REL_PATH", "CorpusVerification", "load_corpus", "verify_corpus"]
