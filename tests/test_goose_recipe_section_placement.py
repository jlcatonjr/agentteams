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


FRESH = DISK.replace("  # Navigator\n  <!-- AGENTTEAMS:END content -->\n",
                     "  # Navigator\n  <!-- AGENTTEAMS:END content -->\n  <!-- AGENTTEAMS:BEGIN code_index_consultation v=1 -->\n"
                     "  ## Code-index consultation\n  Use the index.\n  <!-- AGENTTEAMS:END code_index_consultation -->\n")


def test_a_stranded_section_is_moved_back_and_the_recipe_parses():
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(STRANDED)
    assert repair_misplaced_sections(STRANDED) == (STRANDED, [])              # no fresh render: never moves
    out, notices = repair_misplaced_sections(STRANDED, FRESH)
    loaded = yaml.safe_load(out)
    assert "Use the index." in loaded["instructions"] and [e["name"] for e in loaded["extensions"]] == ["developer"]
    assert notices and "code_index_consultation" in notices[0]
    assert repair_misplaced_sections(out, FRESH) == (out, [])                 # idempotent


def test_an_unterminated_stranded_section_is_left_alone():
    broken = DISK + "\n  <!-- AGENTTEAMS:BEGIN x v=1 -->\n  no end\n"
    out, notices = repair_misplaced_sections(broken, FRESH.replace("code_index_consultation", "x"))
    assert out == broken and "no END" in notices[0]


def test_merge_repairs_a_stranded_recipe(tmp_path: Path):
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "navigator.yaml").write_text(STRANDED, encoding="utf-8")
    res = emit.emit_all([("navigator.yaml", FRESH)], output_dir=recipes, merge=True, yes=True)
    loaded = yaml.safe_load((recipes / "navigator.yaml").read_text(encoding="utf-8"))
    assert "Use the index." in loaded["instructions"]
    assert [e["name"] for e in loaded["extensions"]] == ["developer"]


def test_a_marker_pair_inside_another_block_scalar_is_never_moved():
    """@security: a prompt: | (or response: >) block that quotes a section must stay where it is."""
    text = DISK + ('prompt: |\n  <!-- AGENTTEAMS:BEGIN code_index_consultation v=1 -->\n  quoted\n'
                   '  <!-- AGENTTEAMS:END code_index_consultation -->\n')
    assert repair_misplaced_sections(text, FRESH) == (text, [])


def test_a_section_spanning_a_top_level_key_is_left_alone():
    text = DISK.replace("extensions:", "  <!-- AGENTTEAMS:BEGIN code_index_consultation v=1 -->\n  x\nextensions:", 1) + \
        "  <!-- AGENTTEAMS:END code_index_consultation -->\n"
    out, notices = repair_misplaced_sections(text, FRESH)
    assert out == text


def test_a_render_without_extensions_never_loses_the_on_disk_key(tmp_path: Path):
    """@security: if the repair dropped `extensions:` and the render has none, Goose would load the user's extensions."""
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "navigator.yaml").write_text(STRANDED, encoding="utf-8")
    fresh_no_ext = FRESH.split("extensions:")[0]
    emit.emit_all([("navigator.yaml", fresh_no_ext)], output_dir=recipes, merge=True, yes=True)
    loaded = yaml.safe_load((recipes / "navigator.yaml").read_text(encoding="utf-8"))
    assert [e["name"] for e in loaded["extensions"]] == ["developer"]


def test_append_section_only_redirects_for_goose_recipes():
    md = "# Doc\n\n```yaml\ninstructions: |\n  x\nother: y\n```\n"
    assert _append_section(md, "block\n").endswith("block\n")


def test_a_section_already_inside_the_instructions_is_not_duplicated():
    inside = FRESH + ('  <!-- AGENTTEAMS:BEGIN code_index_consultation v=1 -->\n  stray copy\n'
                      '  <!-- AGENTTEAMS:END code_index_consultation -->\n')
    out, _ = repair_misplaced_sections(inside, FRESH)
    assert out.count("AGENTTEAMS:BEGIN code_index_consultation") == 2 and out == inside
