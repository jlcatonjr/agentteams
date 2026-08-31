"""Tests for read-only-auditor tool scope (3.3 from the 2026-05-27 efficiency review).

Audits agent templates whose role descriptions assert read-only behaviour
('detects', 'reviews', 'audits', 'enforces' without 'rewrites') and asserts
their declared tools omit write/execute capabilities. Tighter scoping
reduces tool-schema tokens that the consumer harness loads at runtime.

The list is curated, not derived from the description — a heuristic
extractor would mis-classify edge cases. New auditor-class templates
should be added to READ_ONLY_AGENT_TEMPLATES with the same audit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "agentteams" / "templates" / "universal"

# Curated list of templates whose declared role is pure audit. Adding a new
# entry asserts the contract; removing one requires a description-text update
# explaining why writes are necessary.
READ_ONLY_AGENT_TEMPLATES = (
    "security",
    "adversarial",
    "code-hygiene",
    "conflict-auditor",
)

# Tools that mutate the working tree or invoke shell. Any of these in a
# read-only agent's tool list is a contract violation.
WRITE_CLASS_TOOLS = frozenset({"edit", "execute", "write"})

_TOOLS_LINE_RE = re.compile(r"^tools:\s*\[(.*?)\]\s*$", re.MULTILINE)
_TOKEN_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")


def _parse_tools(template_text: str) -> list[str]:
    m = _TOOLS_LINE_RE.search(template_text)
    if not m:
        return []
    return [a or b for a, b in _TOKEN_RE.findall(m.group(1))]


@pytest.mark.parametrize("slug", READ_ONLY_AGENT_TEMPLATES)
def test_read_only_agent_declares_no_write_tools(slug):
    tpl = TEMPLATES_DIR / f"{slug}.template.md"
    assert tpl.exists(), f"missing template: {tpl}"
    tools = _parse_tools(tpl.read_text(encoding="utf-8"))
    write_tools = set(tools) & WRITE_CLASS_TOOLS
    assert not write_tools, (
        f"{slug} is in READ_ONLY_AGENT_TEMPLATES but declares write-class "
        f"tools {sorted(write_tools)}. Either remove the over-scoped tools "
        f"or update the role description and drop {slug} from the read-only "
        f"set."
    )


def test_parse_tools_handles_single_and_double_quotes():
    assert _parse_tools("tools: ['read', 'search']\n") == ["read", "search"]
    assert _parse_tools('tools: ["read", "search"]\n') == ["read", "search"]


def test_parse_tools_returns_empty_when_no_tools_line():
    assert _parse_tools("name: x\ndescription: y\n") == []
