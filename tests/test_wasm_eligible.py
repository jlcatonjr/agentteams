"""P01 D9 / METAPLAN R9, operator decision D12: ``wasm_eligible`` — the agent holds an execute-class tool.

Defined by tools, not by a slug list: every shipped template holding ``execute`` is eligible (read-only ones such as
``navigator`` included: moving a shell holder to compute-only ``run_wasm`` is a narrowing), every template without it
is not. Missing or unknown tools -> False. The agents that need native exec keep it via ``native_exec_keep``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import sandbox_state as ss
from agentteams.capability_map import canonical_tools_for_copilot_vscode
from agentteams.wasm_config import wasm_eligible

TEMPLATES = sorted((Path(__file__).resolve().parent.parent / "agentteams" / "templates").rglob("*.template.md"))
#: Read-only auditors (no ``edit``); per D12 the edit scope no longer decides eligibility.
_AUDITORS = ("security", "adversarial", "code-hygiene", "conflict-auditor", "navigator", "quality-auditor",
             "technical-validator")


@pytest.mark.parametrize("tools, expected", [
    (["read", "edit", "search", "execute"], True),
    (["edit", "execute"], True),
    (["Read", "Edit", "Bash"], True),                  # Claude tool names map to canonical scopes
    (["read", "search", "execute"], True),             # read-only with execute (navigator): D12, eligible
    (["read", "search", "Bash"], True),
    (["read", "edit", "search"], False),               # no execute class
    (["read", "edit", "retrieval"], False),            # the scoped retrieval grant is not execute
    ([], False),
    (None, False),
    ("read, edit, execute", False),                    # not a list: fail toward not offering a tool
    ([1, "edit", "execute"], False),
])
def test_predicate(tools, expected):
    assert wasm_eligible({"slug": "x", "tools": tools}) is expected


def test_missing_tools_and_non_mapping_are_not_eligible():
    assert wasm_eligible({"slug": "x"}) is False
    assert wasm_eligible(None) is False  # type: ignore[arg-type]
    assert ss.wasm_eligible({"slug": "x", "tools": ["edit", "execute"]}) is True


def _template_tools(path: Path) -> list[str] | None:
    return canonical_tools_for_copilot_vscode(path.read_text(encoding="utf-8"))


def test_every_shipped_template_is_eligible_iff_it_holds_execute():
    seen = set()
    for path in TEMPLATES:
        tools = _template_tools(path)
        if tools is None:
            continue
        slug = path.name.removesuffix(".template.md")
        assert wasm_eligible({"slug": slug, "tools": tools}) is ("execute" in tools), slug
        seen.add(slug)
    assert set(_AUDITORS) <= seen


@pytest.mark.parametrize("slug", _AUDITORS)
def test_read_only_auditors_follow_their_execute_tool(slug):
    """D12: an auditor without a shell is not offered run_wasm; one with a shell (navigator) is."""
    path = next(p for p in TEMPLATES if p.name == f"{slug}.template.md")
    tools = _template_tools(path)
    assert "edit" not in tools
    assert wasm_eligible({"slug": slug, "tools": tools}) is ("execute" in tools)


def test_the_default_keep_agents_are_eligible():
    """The D12 default keep only matters because navigator and git-operations hold execute (they need git)."""
    from agentteams.wasm_config import NATIVE_EXEC_KEEP_DEFAULT

    for slug in NATIVE_EXEC_KEEP_DEFAULT:
        path = next(p for p in TEMPLATES if p.name == f"{slug}.template.md")
        assert wasm_eligible({"slug": slug, "tools": _template_tools(path)}) is True, slug
