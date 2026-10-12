"""Pilot 1 findings F1/F2 (biographies, 2026-10-12).

F1: in Claude Code the session that dispatches agents is the top-level one, which reads CLAUDE.md, not the
orchestrator's agent file. Under the switch the instructions file must carry the dispatch contract (a nonce per
dispatch, the approval loop). F2: a granted agent's write receipt is final; ``request_status`` is only for a call
that returned a ``request_id``.
"""
from __future__ import annotations

from agentteams import write_policy as W

_ON = {"write_policy": "orchestrator-only", "mcp_grants": {"producer": {"tools": ["write_file"]}}}


def test_instructions_unchanged_without_the_switch():
    text = "# Team\n\nBody.\n"
    assert W.apply_instructions(text, {}) == text


def test_instructions_get_the_dispatch_contract_under_the_switch():
    out = W.apply_instructions("# Team\n\nBody.\n", _ON)
    assert "## Write Policy: You Are the Orchestrator" in out
    assert "--issue-dispatch --agent <slug>" in out and "--apply-staged SID" in out
    assert out.count("## Write Policy:") == 1


def test_fenced_instructions_get_their_own_fence():
    text = "<!-- AGENTTEAMS:BEGIN content v=1 -->\nx\n<!-- AGENTTEAMS:END content -->\n"
    out = W.apply_instructions(text, _ON)
    assert "<!-- AGENTTEAMS:BEGIN write_policy v=1 -->" in out and out.endswith("<!-- AGENTTEAMS:END write_policy -->\n")


def test_granted_agents_are_told_a_receipt_is_final():
    section = W._section("producer", _ON)
    assert "receipt is final" in section and "don't call `request_status`" in section
