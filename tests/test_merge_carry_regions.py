"""`--update --merge` keeps the user regions of a file it full-replaces (#533, 2026-11-16).

A non-Markdown file with no AGENTTEAMS fence on disk or in the fresh render is full-replaced by the merge
(`fences._is_machine_managed_merge_overwrite_path`). A Goose recipe built from an unfenced template (the
work-summarizer, for one) is such a file, and its `## Project-Specific Notes` region sits outside every fence by
design, so the replace dropped the team's notes with no notice (mathAgents, v1.1.3). The merge now carries the
on-disk regions into the render, and refuses the write, keeping the file, when a region can't be carried.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import emit
from agentteams.project_notes import _ensure_project_notes_section

yaml = pytest.importorskip("yaml")

_RECIPE = ('version: "1.0.0"\ntitle: "Work Summarizer"\ndescription: "Summarizes work"\ninstructions: |\n'
           "  # Work Summarizer\n\n  Template body {v}.\n")
_NOTE = "  - Daily summaries also list the open PRs (hand note kept by the project).\n"
_REL = "work-summarizer.yaml"


def _recipes(tmp_path: Path, on_disk: str) -> Path:
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / _REL).write_text(on_disk, encoding="utf-8")
    return recipes


def _fresh(version: str) -> str:
    return _ensure_project_notes_section(_REL, _RECIPE.format(v=version))


def test_merge_keeps_the_notes_of_an_unfenced_recipe_and_takes_the_new_body(tmp_path: Path):
    recipes = _recipes(tmp_path, _fresh("OLD") + _NOTE)
    res = emit.emit_all([(_REL, _fresh("NEW"))], output_dir=recipes, merge=True, yes=True, shrink_policy="preserve")
    out = (recipes / _REL).read_text(encoding="utf-8")
    assert _NOTE in out, "the hand-written Project-Specific Notes were dropped by the merge"
    assert "Template body NEW." in out and "Template body OLD." not in out
    assert not res.errors
    assert _NOTE.strip() in yaml.safe_load(out)["instructions"]


def test_a_recipe_whose_only_difference_is_its_notes_is_unchanged(tmp_path: Path):
    disk = _fresh("SAME") + _NOTE
    recipes = _recipes(tmp_path, disk)
    res = emit.emit_all([(_REL, _fresh("SAME"))], output_dir=recipes, merge=True, yes=True)
    assert (recipes / _REL).read_text(encoding="utf-8") == disk
    assert str(recipes / _REL) in res.unchanged


def test_the_dry_run_reports_what_the_real_run_does(tmp_path: Path):
    disk = _fresh("SAME") + _NOTE
    recipes = _recipes(tmp_path, disk)
    res = emit.emit_all([(_REL, _fresh("SAME"))], output_dir=recipes, merge=True, yes=True, dry_run=True)
    assert [e.action for e in res.dry_run_report.entries] == ["UNCHANGED"]
    assert (recipes / _REL).read_text(encoding="utf-8") == disk


def test_a_region_the_render_has_no_place_for_refuses_the_write(tmp_path: Path):
    disk = _fresh("OLD") + _NOTE
    recipes = _recipes(tmp_path, disk)
    no_region = 'version: "1.0.0"\ntitle: "Work Summarizer"\nprompt: "no instructions block"\n'
    res = emit.emit_all([(_REL, no_region)], output_dir=recipes, merge=True, yes=True)
    assert (recipes / _REL).read_text(encoding="utf-8") == disk
    assert any(_REL in e and "Project-Specific Notes" in e for e in res.errors)


def test_discard_user_regions_still_full_replaces(tmp_path: Path):
    recipes = _recipes(tmp_path, _fresh("OLD") + _NOTE)
    emit.emit_all([(_REL, _fresh("NEW"))], output_dir=recipes, merge=True, yes=True, discard_user_regions=True)
    assert (recipes / _REL).read_text(encoding="utf-8") == _fresh("NEW")


def test_an_unchanged_region_is_not_reported(tmp_path: Path):
    recipes = _recipes(tmp_path, _fresh("OLD"))
    res = emit.emit_all([(_REL, _fresh("NEW"))], output_dir=recipes, merge=True, yes=True)
    assert (recipes / _REL).read_text(encoding="utf-8") == _fresh("NEW")
    assert not res.errors


_TOML = ('name = "work-summarizer"\ndescription = "Summarizes work"\ndeveloper_instructions = \'\'\'\n'
         "# Work Summarizer\n\nTemplate body {v}.\n\n## Project-Specific Notes\n{notes}'''\n")


def test_an_unfenced_toml_agent_keeps_its_notes_and_still_parses(tmp_path: Path):
    import tomllib
    agents = tmp_path / ".codex" / "agents"
    agents.mkdir(parents=True)
    (agents / "work-summarizer.toml").write_text(_TOML.format(v="OLD", notes="- hand note\n"), encoding="utf-8")
    res = emit.emit_all([("work-summarizer.toml", _TOML.format(v="NEW", notes=""))], output_dir=agents, merge=True,
                        yes=True)
    out = (agents / "work-summarizer.toml").read_text(encoding="utf-8")
    assert not res.errors
    body = tomllib.loads(out)["developer_instructions"]
    assert "Template body NEW." in body and "- hand note" in body


def test_keys_after_a_toml_region_are_not_carried(tmp_path: Path):
    import tomllib
    agents = tmp_path / ".codex" / "agents"
    agents.mkdir(parents=True)
    # Keys a hand edit put after the string's closing ''' are not part of the region (it ends at that line), so
    # the full-replace drops them and the carried text can never close the string and add keys.
    disk = _TOML.format(v="OLD", notes="- note\n").replace("- note\n'''\n", "- note\n'''\ninjected = true\n")
    (agents / "work-summarizer.toml").write_text(disk, encoding="utf-8")
    res = emit.emit_all([("work-summarizer.toml", _TOML.format(v="NEW", notes=""))], output_dir=agents, merge=True,
                        yes=True)
    loaded = tomllib.loads((agents / "work-summarizer.toml").read_text(encoding="utf-8"))
    assert not res.errors
    assert "injected" not in loaded and "- note" in loaded["developer_instructions"]


def test_the_dry_run_reports_a_refusal(tmp_path: Path):
    disk = _fresh("OLD") + _NOTE
    recipes = _recipes(tmp_path, disk)
    no_region = 'version: "1.0.0"\ntitle: "Work Summarizer"\nprompt: "no instructions block"\n'
    res = emit.emit_all([(_REL, no_region)], output_dir=recipes, merge=True, yes=True, dry_run=True)
    assert [e.action for e in res.dry_run_report.entries] == ["UNCHANGED"]
    assert any(_REL in n and "would not merge" in n for n in res.dry_run_report.notices)


def test_a_notes_heading_moved_to_column_zero_is_refused_not_swallowed(tmp_path: Path):
    disk = (_fresh("OLD") + _NOTE).replace("  ## Project-Specific Notes", "## Project-Specific Notes") + "extra: kept\n"
    recipes = _recipes(tmp_path, disk)
    res = emit.emit_all([(_REL, _fresh("NEW"))], output_dir=recipes, merge=True, yes=True)
    assert (recipes / _REL).read_text(encoding="utf-8") == disk
    assert any("not indented" in e for e in res.errors)


def test_well_formed_notes_round_trip_through_a_yaml_loader(tmp_path: Path):
    notes = ("  - key: value lines, quotes ' \" and a colon: stay text\n  - |pipes| and # hashes\n\n"
             "      - deeper indent\n  - a tab\tinside a line\n")
    recipes = _recipes(tmp_path, _fresh("OLD") + notes)
    res = emit.emit_all([(_REL, _fresh("NEW"))], output_dir=recipes, merge=True, yes=True)
    loaded = yaml.safe_load((recipes / _REL).read_text(encoding="utf-8"))
    assert not res.errors
    assert set(loaded) == {"version", "title", "description", "instructions"}
    tail = loaded["instructions"].split("## Project-Specific Notes", 1)[1]
    for line in notes.splitlines():
        assert line[2:] in tail


def test_the_shape_check_refuses_a_carry_that_would_break_the_recipe():
    from agentteams.user_regions import recipe_shape_problems
    fresh = _fresh("NEW")
    good = fresh + _NOTE
    assert recipe_shape_problems(fresh, good) == []
    # A shallower line ends the region early, so what follows it no longer matches the fresh render.
    assert recipe_shape_problems(fresh, fresh + " - one-space line ends the block scalar\n")
    moved_key = fresh.replace("  ## Project-Specific Notes", "  ## Project-Specific Notes\nprompt: injected\n  x")
    assert recipe_shape_problems(fresh, moved_key)
    tab_indent = fresh + "\t- tab-indented\n"
    assert recipe_shape_problems(fresh, tab_indent)
