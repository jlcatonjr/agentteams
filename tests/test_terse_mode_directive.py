"""Tests for the tone_and_style fence in copilot-instructions.template.md (3.5).

The terse-mode directive is a fenced section so consumer repos pick it up
via `--update --merge`. The test asserts the fence exists with the expected
shape and lists the read-only auditor roles by slug.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "agentteams"
    / "templates"
    / "copilot-instructions.template.md"
)

_FENCE_RE = re.compile(
    r"<!-- AGENTTEAMS:BEGIN tone_and_style v=\d+ -->.*?"
    r"<!-- AGENTTEAMS:END tone_and_style -->",
    re.DOTALL,
)


def test_tone_and_style_fence_present():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert _FENCE_RE.search(text), "tone_and_style fence missing from copilot-instructions template"


def test_tone_and_style_names_read_only_agents():
    text = TEMPLATE.read_text(encoding="utf-8")
    m = _FENCE_RE.search(text)
    assert m is not None
    body = m.group(0)
    # Sample the read-only set that test_agent_tool_scopes already audits.
    for agent in ("@security", "@adversarial", "@code-hygiene", "@conflict-auditor"):
        assert agent in body, f"terse-mode block must list {agent}"


def test_tone_and_style_exempts_producers():
    """Producing roles must be explicitly exempt so they aren't silenced."""
    text = TEMPLATE.read_text(encoding="utf-8")
    m = _FENCE_RE.search(text)
    body = m.group(0)
    for producer in ("@primary-producer", "@module-doc-author"):
        assert producer in body, f"terse-mode block must list {producer} as exempt producer"
