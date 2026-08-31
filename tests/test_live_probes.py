"""Live-tier probes — the external surface that justifies running the audit daily.

These are the only tests in the suite that touch the network, and they are **skipped
unless ``FEATURE_AUDIT_LIVE=1``**. CI stays deterministic; the daily audit sets the
variable. Putting them behind an env gate rather than a marker avoids changing
``addopts``, which would alter collection for every other test in the repo.

Two rules govern everything here.

**Assert positive content, never absence of error.** ``agentteams/research/`` is
degrade-don't-raise by policy (``references/retrieval-transport-policy.md``): a failed
search returns an empty result rather than raising. A probe that only checks "no
exception" therefore passes when the feature is completely dead — a proof that cannot
fail, which is the exact defect the whole feature-audit workstream exists to find.

**Reach the network only through ``python -m agentteams.research``.** That CLI is where
the SSRF guard, host allowlists and size caps live. Opening a socket here would create the
second egress path the transport policy exists to prevent, and would make these probes
test something other than the shipped code path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

LIVE_ENABLED = os.environ.get("FEATURE_AUDIT_LIVE") == "1"
requires_live = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="live probes touch the network; set FEATURE_AUDIT_LIVE=1 (the daily audit does)",
)


def _research(*args: str, timeout: int = 90) -> subprocess.CompletedProcess:
    """Invoke the research CLI — the single sanctioned egress path."""
    return subprocess.run(
        [sys.executable, "-m", "agentteams.research", *args],
        cwd=REPO, capture_output=True, text=True, timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@requires_live
def test_research_search_returns_substantive_results():
    """PROOF: search returns real, shaped results — not merely "no error".

    The degraded path returns empty. Asserting non-emptiness plus per-result shape is what
    distinguishes "the allowlisted backends still answer" from "the feature silently died
    and returned nothing."
    """
    proc = _research("search", "python packaging", "-k", "5")
    assert proc.returncode == 0, f"search exited {proc.returncode}: {proc.stderr[-800:]}"

    text = proc.stdout.strip()
    assert text, "search produced no output at all — the degraded path is indistinguishable here"

    # Accept either JSON or the human table, but require substance either way.
    urls: list[str] = []
    try:
        payload = json.loads(text)
        items = payload if isinstance(payload, list) else payload.get("results", [])
        urls = [i.get("url", "") for i in items if isinstance(i, dict)]
    except json.JSONDecodeError:
        urls = [ln for ln in text.splitlines() if "http" in ln]

    assert urls, f"search returned zero results — degraded or blocked. Output:\n{text[:600]}"
    assert any(u.startswith("http") for u in urls), (
        f"no result carried a usable URL; the shape changed upstream:\n{text[:600]}"
    )


def test_the_ssrf_guard_rejects_a_private_address():
    """NEGATIVE CONTROL for search.

    Distinct from the proof and deliberately offline: it fails if the guard that makes the
    single egress path *safe* stops working. Without this, "search returns results" could
    pass while the SSRF protection had been removed entirely — the proof would be green and
    the feature would be dangerous.
    """
    from agentteams.research.search import is_public_https

    assert not is_public_https("http://127.0.0.1/admin")
    assert not is_public_https("http://169.254.169.254/latest/meta-data/")
    assert not is_public_https("https://10.0.0.1/internal")
    assert not is_public_https("ftp://example.com/x")
    assert is_public_https("https://example.com/page")


# ---------------------------------------------------------------------------
# Scholar
# ---------------------------------------------------------------------------

@requires_live
def test_research_scholar_returns_identified_works():
    """PROOF: the scholarly indexes still answer, and still carry identifiers.

    A DOI/id is the thing that makes a scholar result *useful*; asserting only that a title
    came back would pass on a degraded response that cannot be cited.
    """
    proc = _research("scholar", "transformer architecture", "-k", "3")
    assert proc.returncode == 0, f"scholar exited {proc.returncode}: {proc.stderr[-800:]}"

    text = proc.stdout.strip()
    assert text, "scholar produced no output"
    lowered = text.lower()
    assert ("doi" in lowered) or ("arxiv" in lowered) or ("openalex" in lowered), (
        f"scholar returned no identifier-bearing result:\n{text[:600]}"
    )


def test_scholar_host_allowlist_is_enforced():
    """NEGATIVE CONTROL for scholar.

    Fails if the host allowlist is emptied or bypassed — the constraint that keeps the
    scholarly path from becoming an open fetcher.
    """
    from agentteams.research import scholarly

    allowed = getattr(scholarly, "_ALLOWED_HOSTS", None)
    assert allowed, "scholarly._ALLOWED_HOSTS is missing or empty — the allowlist is gone"
    assert not any("evil" in h for h in allowed)
    assert any("openalex" in h or "crossref" in h or "arxiv" in h for h in allowed), (
        f"the allowlist no longer contains the expected scholarly hosts: {sorted(allowed)}"
    )
