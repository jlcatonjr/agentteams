"""`--update --merge` on Goose recipes keeps enriched fenced bodies and valid YAML (2026-10-10).

A goose recipe whose template has no fence renders fence-less. The merge-overwrite gate looked only at the fresh
render, so it full-replaced every such recipe — including ones whose on-disk `content` fence held a hand-enriched
body (14–15 recipes each in healthResearch and SocialScienceHumanities). Separately, inserting a new section before
an indented marker stranded the indent and pulled the marker to column 0, which ended the YAML block scalar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import emit
from agentteams.fences import (
    _is_machine_managed_merge_overwrite_path,
    _kept_fenced_sections,
    _merge_fenced_content,
    _write_lost_fence_sidecars,
)

yaml = pytest.importorskip("yaml")

ENRICHED = '''version: "1.0.0"
title: "Style Guardian — Project"
description: "Enforces voice"
instructions: |
  <!-- AGENTTEAMS:BEGIN content v=1 -->
  # Style Guardian — Project

  1. **Priority 1 — Editorial Patterns (E-series).** Read `references/style/editorial.md` first.
  2. **Priority 2 — AI-Tell Elimination (A-series).**
  <!-- AGENTTEAMS:END content -->

  ## Project-Specific Notes
  - hand note kept by the project
'''

FRESH_UNFENCED = '''version: "1.0.0"
title: "Style Guardian — Project"
description: "Enforces voice"
instructions: |
  # Style Guardian — Project

  **Style reference:** `N/A - no formal style guide defined for this project`
'''


def test_the_gate_never_full_replaces_a_file_fenced_on_disk():
    assert not _is_machine_managed_merge_overwrite_path("style-guardian.yaml", FRESH_UNFENCED, ENRICHED)
    # Unfenced on disk and in the render: still a full replace (unchanged behaviour).
    assert _is_machine_managed_merge_overwrite_path("style-guardian.yaml", FRESH_UNFENCED, FRESH_UNFENCED)
    assert _is_machine_managed_merge_overwrite_path("style-guardian.yaml", FRESH_UNFENCED)
    assert not _is_machine_managed_merge_overwrite_path("agent.md", FRESH_UNFENCED, ENRICHED)
    assert _kept_fenced_sections("style-guardian.yaml", FRESH_UNFENCED, ENRICHED)
    assert not _kept_fenced_sections("style-guardian.yaml", ENRICHED, ENRICHED)


def test_merge_keeps_an_enriched_recipe_byte_for_byte(tmp_path: Path):
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    target = recipes / "style-guardian.yaml"
    target.write_text(ENRICHED, encoding="utf-8")
    res = emit.emit_all([("style-guardian.yaml", FRESH_UNFENCED)], output_dir=recipes, merge=True, yes=True,
                        shrink_policy="preserve")
    assert target.read_text(encoding="utf-8") == ENRICHED
    assert any("on-disk fenced sections are kept" in n for n in res.notices)
    yaml.safe_load(target.read_text(encoding="utf-8"))


def test_an_inserted_section_keeps_the_anchor_indented():
    disk = 'title: x\ninstructions: |\n  intro\n  <!-- AGENTTEAMS:BEGIN b v=1 -->\n  B\n  <!-- AGENTTEAMS:END b -->\n'
    new = ('title: x\ninstructions: |\n  intro\n  <!-- AGENTTEAMS:BEGIN a v=1 -->\n  A\n  <!-- AGENTTEAMS:END a -->\n'
           '  <!-- AGENTTEAMS:BEGIN b v=1 -->\n  B\n  <!-- AGENTTEAMS:END b -->\n')
    merged = _merge_fenced_content(new, disk, preserve_on_shrink=True, rel_path="x.yaml").merged_content
    for line in merged.splitlines():
        if "AGENTTEAMS:" in line:
            assert line.startswith("  "), f"marker pulled out of the block scalar: {line!r}"
    loaded = yaml.safe_load(merged)
    assert "AGENTTEAMS:BEGIN a" in loaded["instructions"] and "AGENTTEAMS:BEGIN b" in loaded["instructions"]


def test_every_merged_recipe_parses(tmp_path: Path):
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    disk = ('version: "1.0.0"\ntitle: "Q"\ninstructions: |\n  head\n  <!-- AGENTTEAMS:BEGIN rules v=1 -->\n'
            '  old rules\n  <!-- AGENTTEAMS:END rules -->\n')
    fresh = ('version: "1.0.0"\ntitle: "Q"\ninstructions: |\n  head\n  <!-- AGENTTEAMS:BEGIN intro v=1 -->\n'
             '  new intro\n  <!-- AGENTTEAMS:END intro -->\n  <!-- AGENTTEAMS:BEGIN rules v=1 -->\n'
             '  new rules\n  <!-- AGENTTEAMS:END rules -->\n')
    (recipes / "quality-auditor.yaml").write_text(disk, encoding="utf-8")
    emit.emit_all([("quality-auditor.yaml", fresh)], output_dir=recipes, merge=True, yes=True)
    loaded = yaml.safe_load((recipes / "quality-auditor.yaml").read_text(encoding="utf-8"))
    assert "new intro" in loaded["instructions"]


def test_a_sidecar_for_a_path_above_the_team_dir_stays_in_the_backup(tmp_path: Path):
    backup = tmp_path / ".goose" / "recipes" / ".agentteams-backups" / "TS"
    written = _write_lost_fence_sidecars(backup, "../../.goosehints", {"content": "body"})
    path = Path(written["content"])
    assert path.resolve().is_relative_to(backup.resolve())
    assert not (tmp_path / ".goose" / "recipes" / ".goosehints.lost.content.md").exists()


WIDE = '''version: "1.0.0"
title: "Style Guardian — Project"
description: "Enforces voice"
extensions:
  - type: builtin
    name: developer
    bundled: true
    timeout: 300
instructions: |
  <!-- AGENTTEAMS:BEGIN content v=1 -->
  # Style Guardian — Project
  Enriched body.
  <!-- AGENTTEAMS:END content -->
'''

NARROW_FRESH = '''version: "1.0.0"
title: "Style Guardian — Project"
description: "Enforces voice"
extensions:
  - type: builtin
    name: developer
    bundled: true
    timeout: 300
    available_tools: [tree]
instructions: |
  # Style Guardian — Project
  Generic template body.
'''


def test_a_kept_recipe_still_takes_the_templates_tool_grants(tmp_path: Path):
    """@security: keeping the fenced instructions must not freeze a wider on-disk extensions/available_tools."""
    from agentteams.frameworks.goose_recipe_read import recipe_extension_grants

    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    target = recipes / "style-guardian.yaml"
    target.write_text(WIDE, encoding="utf-8")
    res = emit.emit_all([("style-guardian.yaml", NARROW_FRESH)], output_dir=recipes, merge=True, yes=True)
    out = target.read_text(encoding="utf-8")
    assert "Enriched body." in out and "Generic template body." not in out          # instructions kept
    assert recipe_extension_grants(out) == recipe_extension_grants(NARROW_FRESH)      # grants follow the template
    assert any("extensions: aligned with the template" in n and "developer tools" in n for n in res.notices)
    yaml.safe_load(out)


def test_extensions_reconcile_is_idempotent_and_leaves_an_absent_fresh_key_alone():
    from agentteams.frameworks.goose_recipe_merge import reconcile_extensions

    once, _ = reconcile_extensions(NARROW_FRESH, WIDE)
    twice, notices = reconcile_extensions(NARROW_FRESH, once)
    assert twice == once and notices == []
    no_key = NARROW_FRESH.replace("extensions:\n  - type: builtin\n    name: developer\n    bundled: true\n"
                                  "    timeout: 300\n    available_tools: [tree]\n", "")
    assert reconcile_extensions(no_key, WIDE) == (WIDE, [])


def test_a_refused_sidecar_is_reported(tmp_path: Path, capsys):
    backup = tmp_path / "b"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "link").symlink_to(outside)
    assert _write_lost_fence_sidecars(backup, "link/x.md", {"content": "body"}) == {}
    assert "refused" in capsys.readouterr().err


def test_a_marker_stranded_at_column_zero_is_re_indented(tmp_path: Path):
    """researchteam's quality-auditor.yaml on main: an earlier merge left a BEGIN marker at column 0."""
    stranded = ('version: "1.0.0"\ntitle: "Q"\ninstructions: |\n  <!-- AGENTTEAMS:BEGIN a v=1 -->\n  A\n'
                '  <!-- AGENTTEAMS:END a -->\n\n<!-- AGENTTEAMS:BEGIN b v=4 -->\n  B\n  <!-- AGENTTEAMS:END b -->\n')
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(stranded)
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "quality-auditor.yaml").write_text(stranded, encoding="utf-8")
    res = emit.emit_all([("quality-auditor.yaml", stranded.replace("\n<!-- AGENTTEAMS:BEGIN b", "\n  <!-- AGENTTEAMS:BEGIN b"))],
                        output_dir=recipes, merge=True, yes=True)
    out = (recipes / "quality-auditor.yaml").read_text(encoding="utf-8")
    assert "B" in yaml.safe_load(out)["instructions"]                     # the merged recipe parses
    from agentteams.frameworks.goose_recipe_merge import repair_stranded_markers

    repaired, notices = repair_stranded_markers(stranded)                  # the repair itself
    assert notices and "re-indented 1 AGENTTEAMS marker" in notices[0]
    assert "B" in yaml.safe_load(repaired)["instructions"]
    assert repair_stranded_markers(repaired) == (repaired, [])             # idempotent


@pytest.mark.parametrize("hostile", [
    WIDE.replace("instructions: |", "extensions: []\ninstructions: |"),                   # duplicate key
    WIDE.replace("extensions:\n", "extensions:\n---\n"),                                 # document marker in the span
    WIDE.replace("    timeout: 300\n", "    timeout: 300\n- not: [an, extension\n"),     # unparseable entry
])
def test_a_hostile_extensions_shape_never_corrupts_the_recipe(hostile):
    """@security re-verification: a mis-span must roll back (or align exactly), never touch the instructions."""
    from agentteams.frameworks.goose_recipe_merge import reconcile_extensions
    from agentteams.frameworks.goose_recipe_read import recipe_extension_grants

    out, notices = reconcile_extensions(NARROW_FRESH, hostile)
    assert "Enriched body." in out and "<!-- AGENTTEAMS:BEGIN content v=1 -->" in out
    if out != hostile:   # an edit happened: it must reproduce the template's grants exactly
        assert recipe_extension_grants(out) == recipe_extension_grants(NARROW_FRESH)
    else:
        assert notices, "an unchanged hostile recipe must say why"
