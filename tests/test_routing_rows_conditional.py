"""Routing rows for optional agents must say so (issue #392 item 14).

The orchestrator's Domain Agent Routing table routes work to agents the analyzer may not select. A row for
such an agent must carry "*(if in team)*", or the orchestrator routes to an agent that does not exist. The
cross-reference check skips routing-table rows, so this asserts the rows directly: in the template, and in
the rendered example teams, including ones that lack some of these agents.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.analyze import ALWAYS_INCLUDED_DOMAIN_AGENTS

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "agentteams" / "templates"
ORCH = TEMPLATES / "universal" / "orchestrator.template.md"
DOMAIN = TEMPLATES / "domain"
# Domain agents every team gets (analyze.py always adds these), so their rows need no marker.
ALWAYS_SELECTED = frozenset({"primary-producer", "quality-auditor", *ALWAYS_INCLUDED_DOMAIN_AGENTS})
ITEM_14 = ("technical-validator", "format-converter", "reference-manager", "output-compiler")
_ROW = re.compile(r"^\|[^|]*\|\s*`@([a-z0-9-]+)`([^|]*)\|", re.M)


def _routing_rows(text: str) -> list[tuple[str, str]]:
    body = re.split(r"<!-- AGENTTEAMS:BEGIN routing_table_rows v=\d+ -->", text, maxsplit=1)[1]
    body = body.split("<!-- AGENTTEAMS:END routing_table_rows -->", 1)[0]
    return _ROW.findall(body)


def _optional(slug: str) -> bool:
    return (DOMAIN / f"{slug}.template.md").is_file() and slug not in ALWAYS_SELECTED


def test_every_optional_agent_row_in_the_template_is_conditional() -> None:
    rows = _routing_rows(ORCH.read_text(encoding="utf-8"))
    seen = {slug for slug, _ in rows}
    assert set(ITEM_14) <= seen
    bare = sorted(slug for slug, rest in rows if _optional(slug) and "*(if in team)*" not in rest)
    assert not bare, f"routing rows for optional agents lack '*(if in team)*': {bare}"


def test_rendered_examples_mark_the_four_rows_conditional() -> None:
    expected = sorted((REPO / "examples").glob("*/expected/orchestrator.agent.md"))
    assert expected
    lacking = 0
    for path in expected:
        rows = dict(_routing_rows(path.read_text(encoding="utf-8")))
        for slug in ITEM_14:
            assert "*(if in team)*" in rows.get(slug, ""), f"{path.relative_to(REPO)}: @{slug} row is unconditional"
            lacking += not (path.parent / f"{slug}.agent.md").is_file()
    assert lacking, "no example team lacks one of the four agents; the rendered check proves nothing"
