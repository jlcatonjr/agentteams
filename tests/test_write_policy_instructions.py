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


def test_the_shipped_default_refuses_the_switch_on_goose(monkeypatch):
    """Pilot 1, Goose round (G1): delegates inherit the orchestrator's write/edit/shell extensions."""
    import inspect

    import pytest

    assert "\nGOOSE_SWITCH_REFUSED = True\n" in inspect.getsource(W)  # shipped default (the test session lowers it)
    monkeypatch.delenv(W.GOOSE_OVERRIDE_ENV, raising=False)  # the shipped behaviour, without the suite's override
    brief = {"write_policy": "orchestrator-only", "privilege_profile": "confined"}
    with pytest.raises(ValueError, match="not supported on goose"):
        W.resolve(brief, "goose")
    assert W.resolve(brief, "claude") == "orchestrator-only"


def test_the_override_warns(monkeypatch, capsys):
    monkeypatch.setenv(W.GOOSE_OVERRIDE_ENV, "1")
    assert W.resolve({"write_policy": "orchestrator-only", "privilege_profile": "confined"}, "goose") == "orchestrator-only"
    assert "NOT enforced" in capsys.readouterr().err


def test_goose_renders_under_the_override_carry_the_unenforced_banner():
    goose = {**_ON, "framework": "goose"}
    assert "UNENFORCED ON GOOSE" in W._section("producer", goose)
    assert "UNENFORCED ON GOOSE" not in W._section("producer", {**_ON, "framework": "claude"})


def test_the_wiring_check_reports_goose_even_with_the_override(tmp_path, monkeypatch):
    from agentteams import runner_mcp

    monkeypatch.setenv(W.GOOSE_OVERRIDE_ENV, "1")
    problems = runner_mcp.wiring_problems(tmp_path, "goose", {"write_policy": "orchestrator-only"}, home=tmp_path)
    assert any("not enforced on goose" in p for p in problems)


def test_interop_goose_renders_and_instructions_carry_the_banner():
    interop = {**_ON, "write_policy_target_framework": "goose"}  # interop's manifest has no "framework"
    assert "UNENFORCED ON GOOSE" in W._section("producer", interop)
    assert "UNENFORCED ON GOOSE" in W.apply_instructions("# Team\n", {**_ON, "framework": "goose"})
    assert "UNENFORCED ON GOOSE" not in W.apply_instructions("# Team\n", {**_ON, "framework": "claude"})
