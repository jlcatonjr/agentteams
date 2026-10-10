"""A new fenced section with no anchor goes inside a recipe's instructions, and stranded ones are moved back.

The merge's last-resort fallback appended a section at the end of the FILE, so in a Goose recipe it landed under
the last top-level key (``extensions:``) and the recipe stopped parsing (researchteam's ``navigator.yaml``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import emit
from agentteams.fences import _append_section
from agentteams.frameworks.goose_recipe_merge import repair_misplaced_sections

yaml = pytest.importorskip("yaml")

DISK = '''version: "1.0.0"
title: "Navigator"
instructions: |
  <!-- AGENTTEAMS:BEGIN content v=1 -->
  # Navigator
  <!-- AGENTTEAMS:END content -->
extensions:
  - type: builtin
    name: developer
'''

STRANDED = DISK + '''
  <!-- AGENTTEAMS:BEGIN code_index_consultation v=1 -->
  ## Code-index consultation
  Use the index.
  <!-- AGENTTEAMS:END code_index_consultation -->
'''


def test_an_unanchored_section_is_appended_inside_the_instructions():
    block = "  <!-- AGENTTEAMS:BEGIN new v=1 -->\n  New.\n  <!-- AGENTTEAMS:END new -->\n"
    out = yaml.safe_load(_append_section(DISK, block))
    assert "New." in out["instructions"] and [e["name"] for e in out["extensions"]] == ["developer"]
    assert _append_section("plain: x\n", "block\n").endswith("block\n")       # non-recipe: end of file


def test_a_stranded_section_is_moved_back_and_the_recipe_parses():
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(STRANDED)
    out, notices = repair_misplaced_sections(STRANDED)
    loaded = yaml.safe_load(out)
    assert "Use the index." in loaded["instructions"] and [e["name"] for e in loaded["extensions"]] == ["developer"]
    assert notices and "code_index_consultation" in notices[0]
    assert repair_misplaced_sections(out) == (out, [])                        # idempotent


def test_an_unterminated_stranded_section_is_left_alone():
    broken = DISK + "\n  <!-- AGENTTEAMS:BEGIN x v=1 -->\n  no end\n"
    out, notices = repair_misplaced_sections(broken)
    assert out == broken and "no END" in notices[0]


def test_merge_repairs_a_stranded_recipe(tmp_path: Path):
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "navigator.yaml").write_text(STRANDED, encoding="utf-8")
    fresh = DISK.replace("  # Navigator\n", "  # Navigator\n  <!-- AGENTTEAMS:END content -->\n  "
                         "<!-- AGENTTEAMS:BEGIN code_index_consultation v=1 -->\n  ## Code-index consultation\n"
                         "  Use the index.\n", 1).replace("  <!-- AGENTTEAMS:END content -->\nextensions",
                         "  <!-- AGENTTEAMS:END code_index_consultation -->\nextensions", 1)
    res = emit.emit_all([("navigator.yaml", fresh)], output_dir=recipes, merge=True, yes=True)
    loaded = yaml.safe_load((recipes / "navigator.yaml").read_text(encoding="utf-8"))
    assert "Use the index." in loaded["instructions"]
    assert [e["name"] for e in loaded["extensions"]] == ["developer"]
