"""The sandbox-relaxation vocabulary (P15 D1; WP-C2 adds the sixth): six kinds, word-start matching, action ids and grant tokens."""

from __future__ import annotations

import pytest

from agentteams.cli import sandbox_effects as se


def test_six_kinds_in_order():
    assert se.SANDBOX_RELAXATION_KINDS == (
        "sandbox-os-off", "sandbox-wasm-off", "wasm-grant-widen", "native-exec-keep", "wasm-to-os-switch",
        "mcp-approval-auto")


@pytest.mark.parametrize("kind", se.SANDBOX_RELAXATION_KINDS)
@pytest.mark.parametrize("spell", [lambda k: k, lambda k: k.replace("-", "_"), lambda k: k.replace("-", " "),
                                   lambda k: k.upper(), lambda k: f"please {k}:claude now"])
def test_every_kind_matches_in_every_spelling(kind, spell):
    assert se.names_sandbox_relaxation(spell(kind))


@pytest.mark.parametrize("text", ["unsandboxed run", "disable_sandbox", "No Sandbox", "keep native exec",
                                  "os sandbox off", "WASM-to-OS"])
def test_synonyms_match(text):
    assert se.names_sandbox_relaxation(text)


@pytest.mark.parametrize("text", ["xunsandbox", "format the docs", "sandbox on", "", "renative-execution"])
def test_word_start_and_benign_text_do_not_match(text):
    assert not se.names_sandbox_relaxation(text)


def test_tokens_carry_hyphen_underscore_and_space_spellings():
    assert {"native-exec", "native_exec", "native exec", "sandbox-os-off", "sandbox_os_off"} <= set(
        se.SANDBOX_RELAXATION_TOKENS)


def test_action_id_round_trip():
    for kind in se.SANDBOX_RELAXATION_KINDS:
        assert se.relaxation_kind_of(se.action_id(kind, "claude")) == kind
    assert se.action_id(se.SANDBOX_WASM_OFF, se.MODEL_SUBSTRATE_QUALIFIER) == "sandbox-wasm-off:model-substrate"
    assert se.relaxation_kind_of("overwrite") is None and se.relaxation_kind_of("sandbox-os-off") == "sandbox-os-off"
    with pytest.raises(ValueError):
        se.action_id("sandbox-off", "claude")
    with pytest.raises(ValueError):
        se.action_id(se.SANDBOX_OS_OFF, "Claude Code")


def test_token_shapes():
    assert se.read_token("analyst", "data/raw") == "wasm:analyst:read:data/raw"
    assert se.scratch_token("analyst") == "wasm:analyst:scratch"
    assert se.native_exec_token("git-operations") == "native-exec:git-operations"
    assert se.is_relaxation_token("wasm:a:scratch") and se.is_relaxation_token("native-exec:a")
    assert not se.is_relaxation_token("agents:a")


@pytest.mark.parametrize("agent,rel", [("Bad", "x"), ("a", "../x"), ("a", "x/../y"), ("a", "./x"), ("a", "/abs"),
                                       ("a", "x//y"), ("a", "x y"), ("a", ""), ("a", "x/"), ("a", "x:y")])
def test_read_token_refuses_bad_slug_and_paths(agent, rel):
    with pytest.raises(ValueError):
        se.read_token(agent, rel)
