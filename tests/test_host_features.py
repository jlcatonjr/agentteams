"""Tests for agentteams.host_features (Phase 0)."""

from __future__ import annotations

import pytest

from agentteams.host_features import (
    HostFeatureError,
    is_enabled,
    parse_tokens,
    validate,
)


def test_parse_tokens_empty_returns_empty_list():
    assert parse_tokens(None) == []
    assert parse_tokens("") == []
    assert parse_tokens("   ") == []


def test_parse_tokens_single_valid():
    assert parse_tokens("claude:hooks") == ["claude:hooks"]


def test_parse_tokens_multiple_dedupes_and_preserves_order():
    raw = "claude:hooks, claude:subagents,claude:hooks"
    assert parse_tokens(raw) == ["claude:hooks", "claude:subagents"]


def test_parse_tokens_bridge_namespace():
    assert parse_tokens("bridge:copilot-vscode-to-claude:subagents") == [
        "bridge:copilot-vscode-to-claude:subagents"
    ]


def test_parse_tokens_invalid_namespace_raises():
    with pytest.raises(HostFeatureError, match="unknown host-feature namespace"):
        parse_tokens("bogus:hooks")


def test_parse_tokens_unknown_feature_raises():
    with pytest.raises(HostFeatureError, match="unknown feature"):
        parse_tokens("claude:not-a-feature")


def test_parse_tokens_missing_colon_raises():
    with pytest.raises(HostFeatureError, match="must be of the form"):
        parse_tokens("claude_hooks")


def test_validate_accepts_known_token():
    validate("bridge:copilot-vscode-to-claude:hooks")


def test_is_enabled_positive_and_negative():
    features = ["claude:hooks", "bridge:copilot-vscode-to-claude:subagents"]
    assert is_enabled(features, "claude", "hooks")
    assert is_enabled(features, "bridge:copilot-vscode-to-claude", "subagents")
    assert not is_enabled(features, "claude", "schedule")
    assert not is_enabled([], "claude", "hooks")
