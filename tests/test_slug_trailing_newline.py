"""#473 and its siblings: every slug check refuses a trailing newline.

``re.match`` with a pattern ending in ``$`` accepts ``"agent\\n"``, because ``$`` also matches just before a final
newline. Each check below now uses ``fullmatch``.
"""
from __future__ import annotations

import pytest

from agentteams import sandbox_handoff, sandbox_tokens
from agentteams.cli import adopt_merge_gate as amg


@pytest.mark.parametrize("slug", ["agent\n", "agent\r\n", "\nagent"])
def test_adopt_merge_gate_refuses(slug):
    with pytest.raises(amg.AdoptMergeError):
        amg._check_slugs([slug])


def test_sandbox_tokens_refuses():
    with pytest.raises(ValueError):
        sandbox_tokens.checked_slug("agent\n", "agent")
    assert sandbox_tokens.checked_slug("agent-1", "agent") == "agent-1"


@pytest.mark.parametrize("pattern", [sandbox_handoff._SLUG_RE, sandbox_tokens._SLUG_RE])
def test_patterns_reject_a_newline_under_fullmatch(pattern):
    assert pattern.fullmatch("agent") and not pattern.fullmatch("agent\n")


def test_no_slug_check_uses_match_any_more():
    from pathlib import Path

    import re

    root = Path(__file__).resolve().parent.parent / "agentteams"
    call = re.compile(r"\b_\w*SLUG\w*_RE\.match\(")          # _SLUG_RE, _SAFE_SLUG_RE, _SLUG_VALUE_RE, ...
    offenders = [str(p.relative_to(root)) for p in root.rglob("*.py") if call.search(p.read_text("utf-8"))]
    assert not offenders, offenders


def test_update_lock_slug_values_refuse_a_newline():
    from agentteams import update_lock

    assert update_lock._SLUG_VALUE_RE.fullmatch("claude,goose") and not update_lock._SLUG_VALUE_RE.fullmatch("x\n")
