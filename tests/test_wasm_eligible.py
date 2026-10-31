"""P01 D9 / METAPLAN R9: ``wasm_eligible`` — execute-class tools and never a read-only auditor.

Defined by tools, not by a slug list: every shipped template without ``edit`` (the read-only auditors, and the
read-only agents that hold ``execute`` such as ``navigator``) is not eligible. Missing or unknown tools -> False.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import sandbox_state as ss
from agentteams.capability_map import canonical_tools_for_copilot_vscode
from agentteams.wasm_config import wasm_eligible

TEMPLATES = sorted((Path(__file__).resolve().parent.parent / "agentteams" / "templates").rglob("*.template.md"))
_AUDITORS = ("security", "adversarial", "code-hygiene", "conflict-auditor", "navigator", "quality-auditor",
             "technical-validator")


@pytest.mark.parametrize("tools, expected", [
    (["read", "edit", "search", "execute"], True),
    (["edit", "execute"], True),
    (["Read", "Edit", "Bash"], True),                  # Claude tool names map to canonical scopes
    (["read", "search", "execute"], False),            # read-only with execute (navigator)
    (["read", "search", "Bash"], False),
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


def test_every_shipped_template_without_edit_is_not_eligible():
    seen = set()
    for path in TEMPLATES:
        tools = _template_tools(path)
        if tools is None:
            continue
        slug = path.name.removesuffix(".template.md")
        if "edit" not in tools:
            assert wasm_eligible({"slug": slug, "tools": tools}) is False, slug
        seen.add(slug)
    assert set(_AUDITORS) <= seen


@pytest.mark.parametrize("slug", _AUDITORS)
def test_the_read_only_auditors_are_locked_out(slug):
    path = next(p for p in TEMPLATES if p.name == f"{slug}.template.md")
    assert wasm_eligible({"slug": slug, "tools": _template_tools(path)}) is False
