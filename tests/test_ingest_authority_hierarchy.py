"""Markdown ingest reads the authority hierarchy, not whatever "source" section comes last (#392 item 11).

Before the fix, any heading containing "source" overwrote ``authority_sources``, so a ``Source Repositories``
table after a numbered ``Authority Hierarchy`` list replaced it. The table's header and separator rows became
tiers, and stripping list markers with ``lstrip`` ate a bold name's leading ``**``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import ingest

TIERS = [
    ("Template library", "agentteams/templates/", "agent file structure"),
    ("JSON schemas", "schemas/", "input/output contracts"),
    ("Python source pipeline", "agentteams/", "pipeline logic"),
    ("Placeholder conventions", "agentteams/templates/PLACEHOLDER-CONVENTIONS.md", "placeholder syntax"),
    ("Implementation plan", "build-team-plan.md", "architectural decisions"),
    ("Book", "docs_src/book/", "narrative"),
]
HIERARCHY = "## Authority Hierarchy\n\n" + "\n".join(
    f"{i}. **{n}** (`{p}`) — {s}" for i, (n, p, s) in enumerate(TIERS, start=1))
SOURCE_TABLE = """## Source Repositories (Read-Only)

| Repository | Path | Purpose |
|---|---|---|
| Book | `../book` | chapters |
| Data | `../data` | raw CSVs |
"""


def _load(tmp_path: Path, body: str) -> dict:
    brief = tmp_path / "brief.md"
    brief.write_text("# Project Name\n\nDemo\n\n## Project Goal\n\nA demo project goal.\n\n" + body,
                     encoding="utf-8")
    return ingest._load_markdown(brief)


@pytest.mark.parametrize("order", ["hierarchy_first", "table_first"])
def test_authority_heading_wins_over_a_source_table(tmp_path: Path, order: str) -> None:
    parts = [HIERARCHY, SOURCE_TABLE] if order == "hierarchy_first" else [SOURCE_TABLE, HIERARCHY]
    sources = _load(tmp_path, "\n\n".join(parts))["authority_sources"]
    assert [(s["name"], s["path"], s.get("scope")) for s in sources] == TIERS
    assert [s["rank"] for s in sources] == list(range(1, 7))


def test_a_source_table_alone_is_parsed_by_column(tmp_path: Path) -> None:
    sources = _load(tmp_path, SOURCE_TABLE)["authority_sources"]
    assert sources == [
        {"name": "Book", "path": "../book", "scope": "chapters", "rank": 1},
        {"name": "Data", "path": "../data", "scope": "raw CSVs", "rank": 2},
    ]


def test_header_words_pick_the_columns() -> None:
    table = "| Purpose | Path | Name |\n|:--|:-:|--:|\n| chapters | ../book | Book |"
    assert ingest._parse_authority_sources(table) == [
        {"name": "Book", "path": "../book", "scope": "chapters", "rank": 1}]


def test_no_tier_is_built_from_table_markup() -> None:
    table = "| Source | Path |\n|---|---|\n| A | a/ |"
    names = [s["name"] for s in ingest._parse_authority_sources(table)]
    assert names == ["A"]


@pytest.mark.parametrize("line, expected", [
    ("1. **Bold** (`p/`) — s", {"name": "Bold", "path": "p/", "scope": "s", "rank": 1}),
    ("- **Bold only**", {"name": "Bold only", "path": "", "rank": 1}),
    ("* Name: path/x — scope", {"name": "Name", "path": "path/x", "scope": "scope", "rank": 1}),
    ("2) name | path", {"name": "name", "path": "path", "rank": 1}),
    ("3D models", {"name": "3D models", "path": "3D models", "rank": 1}),
])
def test_list_items_keep_their_names(line: str, expected: dict) -> None:
    assert ingest._parse_authority_sources(line) == [expected]
