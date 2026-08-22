"""R3 — --update bridge gate (D3): don't silently materialize a native team over a bridge.

The detector must fire on POSITIVE, structured, per-target signals only, and must NOT
false-positive on a native team — in particular on the string `AGENTTEAMS-BRIDGE:BEGIN`
appearing backtick-quoted in constitutional Rule 14 prose inside a native orchestrator body
(the exact bug the adversarial audit caught in the first R3 spec).
"""

from __future__ import annotations

from agentteams.cli.generate import _update_target_is_bridge


def _mk_bridge_manifest(root, pair: str):
    d = root / "references" / "bridges" / pair
    d.mkdir(parents=True)
    (d / "bridge-manifest.json").write_text("{}", encoding="utf-8")


def test_bridge_manifest_for_target_framework_is_detected(tmp_path):
    _mk_bridge_manifest(tmp_path, "goose-to-claude")
    assert _update_target_is_bridge(tmp_path, "claude") is True


def test_manifest_for_other_target_does_not_gate_native_source(tmp_path):
    # goose-to-claude means goose is the canonical SOURCE — a goose --update must NOT gate.
    _mk_bridge_manifest(tmp_path, "goose-to-claude")
    assert _update_target_is_bridge(tmp_path, "goose") is False


def test_structured_fence_in_entry_file_is_detected(tmp_path):
    cdir = tmp_path / ".claude"
    cdir.mkdir()
    (cdir / "README.md").write_text(
        "# Bridge\n<!-- AGENTTEAMS-BRIDGE:BEGIN claude-bridge-readme v=1 -->\nx\n"
        "<!-- AGENTTEAMS-BRIDGE:END claude-bridge-readme -->\n",
        encoding="utf-8",
    )
    assert _update_target_is_bridge(tmp_path, "claude") is True


def test_rule14_backtick_prose_in_agent_body_is_NOT_a_bridge(tmp_path):
    # THE false-positive the audit caught: a native orchestrator quotes the marker string in
    # Rule 14 prose. It is NOT inside a scanned entry file, and even if it were, the substring
    # is not the structured HTML-comment fence. Must classify as native.
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "orchestrator.md").write_text(
        "Rule 14: confirm each file carries an `AGENTTEAMS-BRIDGE:BEGIN` fence.\n",
        encoding="utf-8",
    )
    # Also drop the same backtick prose into an ENTRY file to prove the regex (not a
    # substring) is what gates: backtick prose lacks the <!-- ... v=N --> structure.
    cdir = tmp_path / ".claude"
    (cdir / "README.md").write_text(
        "See Rule 14: an `AGENTTEAMS-BRIDGE:BEGIN` fence is required.\n", encoding="utf-8"
    )
    assert _update_target_is_bridge(tmp_path, "claude") is False


def test_native_team_with_no_signals_is_not_a_bridge(tmp_path):
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    assert _update_target_is_bridge(tmp_path, "claude") is False
