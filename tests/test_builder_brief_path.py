"""The team builder writes into the brief agentteams already resolves, and never shadows it (A5).

``fleet._resolve_descriptor`` reads ``.agentteams/brief.json``, then ``brief.json``, then the thin stub. A builder
that always created ``.agentteams/brief.json`` would shadow an existing root ``brief.json`` (a researchteam-derived
project keeps its brief there), so every builder template names the resolution order, updates an existing brief in
place, creates ``.agentteams/brief.json`` only when none exists, and passes the chosen file to the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.fleet import _resolve_descriptor

BUILDER = Path(__file__).resolve().parents[1] / "agentteams" / "templates" / "builder"
TEMPLATES = sorted(BUILDER.glob("team-builder-*.template.md"))


def test_there_are_builder_templates() -> None:
    assert len(TEMPLATES) >= 5


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_builder_updates_the_resolved_brief_and_creates_one_only_when_none_exists(path: Path) -> None:
    text = " ".join(path.read_text(encoding="utf-8").split())
    rich, root = text.find("`.agentteams/brief.json` if it exists"), text.find("else `brief.json` at the project root")
    assert -1 < rich < root, path.name                       # the order fleet resolves
    assert "Update an existing brief in place" in text and "keep every key you did not ask about" in text
    assert "Create `.agentteams/brief.json` only when neither file exists" in text
    assert "--description <brief>" in text and "--description .agentteams/brief.json" not in text


def test_the_documented_order_is_the_resolver_order(tmp_path: Path) -> None:
    assert _resolve_descriptor(tmp_path) is None
    (tmp_path / "brief.json").write_text("{}")
    assert _resolve_descriptor(tmp_path) == tmp_path / "brief.json"
    (tmp_path / ".agentteams").mkdir()
    (tmp_path / ".agentteams" / "brief.json").write_text("{}")
    assert _resolve_descriptor(tmp_path) == tmp_path / ".agentteams" / "brief.json"
