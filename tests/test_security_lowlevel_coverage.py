"""Coverage guard for the @security agent's low-level / systems vulnerability
screening (F-CODEIDX-adjacent security feature, 2026-07-09).

The `@security` template historically enumerated only web/service-tier classes
(CWE-79/89/352/862 + slopsquatting + unsanitized-output-to-sink) plus the OWASP
LLM Top 10. This guard pins the addition of a *low-level, any-language* tier:
arbitrary-code-execution / injection sinks (universal), memory-safety corruption
(native/unsafe surfaces), and an honest hardware/microarchitectural scope
boundary. It also pins the static MITRE CWE threat-intel source and CTRL-11.

See `references/plans/security-low-level-vuln-coverage.{report,plan}.md`.

NOTE on CWE tokens: the template compresses related CWEs into slashed groups
(e.g. `CWE-787/120/121/122`, `CWE-94/95`). Only the LEADING number of a slashed
group is an independent substring — assert `CWE-787`, never `CWE-120`.
"""

from __future__ import annotations

import json
from pathlib import Path

import agentteams
from agentteams import security_refs

_TEMPLATE = (
    Path(agentteams.__file__).parent
    / "templates"
    / "universal"
    / "security.template.md"
)


def _template_text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def test_template_lists_low_level_classes() -> None:
    """The low-level block exists, sits INSIDE the invariant fence, and names
    the representative low-level classes across all three tiers."""
    text = _template_text()

    # Placement: inside the security_rules_invariant fence.
    begin = text.index("<!-- AGENTTEAMS:BEGIN security_rules_invariant")
    end = text.index("<!-- AGENTTEAMS:END security_rules_invariant")
    block = text.index("### Low-Level & Systems Vulnerabilities (Any Language)")
    assert begin < block < end, "low-level block must live inside the invariant fence"

    fence_body = text[begin:end]

    # Leading CWE token of each class/group (never the 2nd/3rd of a slashed group).
    required_tokens = [
        # ACE / injection sinks (universal)
        "CWE-78",   # OS command injection
        "CWE-94",   # code injection / eval (from CWE-94/95)
        "CWE-502",  # unsafe deserialization
        "CWE-22",   # path traversal
        "CWE-918",  # SSRF
        "CWE-611",  # XXE
        "CWE-470",  # unsafe reflection
        "CWE-377",  # insecure temp file
        # memory-safety corruption (native/unsafe surface)
        "CWE-787",  # OOB write (from CWE-787/120/121/122)
        "CWE-125",  # OOB read
        "CWE-416",  # UAF (from CWE-416/415)
        "CWE-190",  # integer overflow (from CWE-190/191)
        "CWE-134",  # format string
        "CWE-843",  # type confusion
        "CWE-367",  # TOCTOU
        # microarchitectural (candidate-flag only)
        "CWE-208",  # non-constant-time comparison
    ]
    missing = [t for t in required_tokens if t not in fence_body]
    assert not missing, f"low-level block missing CWE tokens: {missing}"

    # Surface gating + honest hardware-tier boundary must be stated.
    assert "native/unsafe surface" in fence_body
    assert "Spectre" in fence_body
    assert "out of scope for this agent's per-line review" in fence_body


def test_new_trigger_row_present() -> None:
    """The native/unsafe-memory trigger row is in the Mandatory Review Triggers
    table (inside the invariant fence)."""
    text = _template_text()
    assert "Memory-safety exploit surface (low-level)" in text
    assert "unsafe-memory code" in text


def test_static_cwe_source_registered(tmp_path: Path) -> None:
    """The static MITRE CWE source and CTRL-11 render into the security
    placeholders, without disturbing the pinned OWASP LLM Top-10 count.

    Uses an offline build in a FRESH tmp dir (no cache json), so the in-code
    `sources` list survives (the cache-clobber path fires only when a cache
    exists)."""
    output_dir = tmp_path / ".github" / "agents"
    placeholders = security_refs.build_security_placeholders(
        output_dir=output_dir,
        offline=True,
        max_items=5,
    )

    assert "MITRE CWE" in placeholders["SECURITY_SOURCE_REGISTRY"]
    assert "CTRL-11" in placeholders["SECURITY_CONTROL_EVIDENCE_SUMMARY"]

    # Guard: we must NOT have expanded _OWASP_LLM_TOP10 (pinned elsewhere at 10).
    payload = json.loads(placeholders["SECURITY_VULNERABILITY_WATCH_JSON"])
    assert len(payload["llm_threats"]) == 10
