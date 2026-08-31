"""Propagation guard for the @security 'AI-Authored Code Is Insecure By Default'
screening block (2026-06-03 relocation).

The block must live INSIDE the `security_rules_invariant` fence (not the unfenced
intro) so `--update --merge` delivers it to existing consumer security agents.
This test pins the load-bearing mechanism — a matched fence's body is replaced
with the new render — and the audit-found edge case: a consumer whose fences are
in the LEGACY order (threat_intelligence ABOVE security_rules_invariant) still
receives the block, in place, with no reorder (so the block's reference to the
`threat_intelligence` fence must stay directionless — never 'below').
"""

from __future__ import annotations

from agentteams import emit


def _fenced(sid: str, body: str) -> str:
    return (
        f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n{body}"
        + ("" if body.endswith("\n") else "\n")
        + f"<!-- AGENTTEAMS:END {sid} -->\n"
    )


_BLOCK = (
    "### AI-Authored Code Is Insecure By Default\n\n"
    "Screen AI-authored code for CWE-79/89/352/862, slopsquatting, and "
    "unsanitized output to a sink. The OWASP LLM Top 10 is enumerated in the "
    "`threat_intelligence` fence.\n\n"
    "### Low-Level & Systems Vulnerabilities (Any Language)\n\n"
    "Also screen for arbitrary-code-execution / injection sinks (CWE-78/94/502/"
    "22/918/611), memory-safety corruption on native/unsafe surfaces (CWE-787/"
    "416/190/134), and constant-time / microarchitectural candidates (CWE-208), "
    "routing hardware exploits to specialist tooling.\n"
)


def test_screening_block_propagates_into_existing_invariant_fence():
    # Legacy consumer ordering: threat_intelligence ABOVE security_rules_invariant.
    existing = (
        "# Security\n\n"
        + _fenced("threat_intelligence", "OWASP LLM Top 10: LLM01..LLM10\n")
        + "\n"
        + _fenced("security_rules_invariant", "Rules S-1..S-8 + HALT criteria.\n")
        + "\n## Project-Specific Notes\noperator content here\n"
    )
    # New render: the invariant fence now carries the block. (Its order differs
    # from `existing`; merge must use the EXISTING on-disk order regardless.)
    new = (
        "# Security\n\n"
        + _fenced("security_rules_invariant", "Rules S-1..S-8 + HALT criteria.\n\n" + _BLOCK)
        + "\n"
        + _fenced("threat_intelligence", "OWASP LLM Top 10: LLM01..LLM10\n")
    )
    mr = emit._merge_fenced_content(new, existing)

    # Matched fence replaced; additive change => no shrink, no lost body.
    assert "security_rules_invariant" in mr.sections_replaced
    assert mr.shrink_notices == []
    assert mr.lost_fence_bodies == {}

    out = mr.merged_content
    inv_begin = out.index("BEGIN security_rules_invariant")
    inv_end = out.index("END security_rules_invariant")
    block_at = out.index("AI-Authored Code Is Insecure By Default")
    # Block landed INSIDE the invariant fence.
    assert inv_begin < block_at < inv_end
    # Legacy fence order preserved (merge never reorders).
    assert out.index("BEGIN threat_intelligence") < inv_begin
    # Operator body outside fences preserved.
    assert "operator content here" in out
    # R2 regression guard: the threat-intel reference must be directionless.
    assert "fence below" not in out


def test_unfenced_block_would_not_propagate():
    # Counter-case proving WHY the block had to move into the fence: content in
    # the unfenced body is preserved (never replaced) on merge.
    existing = (
        "# Security\n\nIntro prose without the block.\n\n"
        + _fenced("security_rules_invariant", "Rules S-1..S-8.\n")
    )
    new = (
        "# Security\n\nIntro prose WITH a new screening note.\n\n"
        + _fenced("security_rules_invariant", "Rules S-1..S-8.\n")
    )
    mr = emit._merge_fenced_content(new, existing)
    # The unfenced intro change does NOT propagate — old intro is preserved.
    assert "Intro prose without the block." in mr.merged_content
    assert "new screening note" not in mr.merged_content
