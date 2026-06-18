"""Tests for Goose as a convert TARGET (Goose Phase 4).

`--convert-from <team> --framework goose` produces valid Goose recipes + a repo-root
AGENTS.md + .goosehints; delegation (orchestrator sub_recipes) wires from sources that
preserve handoffs (copilot-vscode). `--interop-from … --framework goose` is intentionally
REFUSED (the CAI representation drops the handoff graph → zero delegation)."""
from __future__ import annotations

import io
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

import build_team
from agentteams.convert import convert_team

_BRIEF = "examples/data-pipeline/brief.json"
pytestmark = pytest.mark.skipif(not Path(_BRIEF).exists(), reason="data-pipeline brief not found")


def _gen_source(root: Path, framework: str) -> Path:
    """Generate a real source team in *root* and return its agents dir."""
    agents = {
        "copilot-vscode": root / ".github" / "agents",
        "claude": root / ".claude" / "agents",
    }[framework]
    rc = build_team.main([
        "--description", _BRIEF, "--framework", framework,
        "--output", str(agents), "--yes", "--no-scan",
    ])
    assert rc == 0
    return agents


@pytest.fixture(scope="module")
def copilot_source(tmp_path_factory):
    return _gen_source(tmp_path_factory.mktemp("cv-src"), "copilot-vscode")


@pytest.fixture(scope="module")
def claude_source(tmp_path_factory):
    return _gen_source(tmp_path_factory.mktemp("cl-src"), "claude")


def _orchestrator_subrecipes(recipes_dir: Path) -> list[str]:
    orch = recipes_dir / "orchestrator.yaml"
    if not orch.exists():
        return []
    return re.findall(r'path:\s*"?\./([A-Za-z0-9_-]+\.yaml)"?', orch.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# convert copilot-vscode -> goose (full fidelity: source keeps handoffs)
# ---------------------------------------------------------------------------

def test_convert_copilot_to_goose_emits_recipes_and_repo_root_files(copilot_source, tmp_path):
    tgt_root = tmp_path / "proj"
    recipes = tgt_root / ".goose" / "recipes"
    res = convert_team(copilot_source, recipes, "goose", project_manifest={"project_name": "P"})
    assert res.errors == []
    assert (recipes / "orchestrator.yaml").exists()
    assert list(recipes.glob("*.yaml"))                      # per-agent recipes
    # repo-root sidecars (two levels above .goose/recipes)
    agents_md = tgt_root / "AGENTS.md"
    assert agents_md.exists(), "AGENTS.md must land at the repo root"
    assert not agents_md.read_text(encoding="utf-8").startswith("---")  # front matter stripped
    assert (tgt_root / ".goosehints").exists()


def test_convert_copilot_to_goose_wires_delegation(copilot_source, tmp_path):
    recipes = tmp_path / "proj" / ".goose" / "recipes"
    convert_team(copilot_source, recipes, "goose", project_manifest={"project_name": "P"})
    subs = _orchestrator_subrecipes(recipes)
    assert subs, "orchestrator must declare sub_recipes (delegation wired from copilot handoffs)"
    # every sub_recipe path must resolve on disk (goose recipe validate does NOT check this)
    unresolved = [s for s in subs if not (recipes / s).exists()]
    assert unresolved == [], f"dangling sub_recipe paths: {unresolved}"


# ---------------------------------------------------------------------------
# convert claude -> goose (valid output; delegation may be flat — claude strips
# handoffs at its own generation, so this is a source-format limitation)
# ---------------------------------------------------------------------------

def test_convert_claude_to_goose_emits_valid_recipes(claude_source, tmp_path):
    tgt_root = tmp_path / "proj"
    recipes = tgt_root / ".goose" / "recipes"
    res = convert_team(claude_source, recipes, "goose", project_manifest={"project_name": "P"})
    assert res.errors == []
    assert (recipes / "orchestrator.yaml").exists()
    assert (tgt_root / "AGENTS.md").exists()
    assert (tgt_root / ".goosehints").exists()


# ---------------------------------------------------------------------------
# F1 backward-compatibility: convert to claude/copilot is unchanged
# ---------------------------------------------------------------------------

def test_convert_to_claude_still_places_claude_md_at_legacy_location(copilot_source, tmp_path):
    tgt_root = tmp_path / "proj"
    recipes = tgt_root / ".claude" / "agents"
    res = convert_team(copilot_source, recipes, "claude", project_manifest={"project_name": "P"})
    assert res.errors == []
    # legacy convention: instructions at target_dir.parent (.claude/CLAUDE.md)
    assert (tgt_root / ".claude" / "CLAUDE.md").exists()
    assert list(recipes.glob("*.md"))


# ---------------------------------------------------------------------------
# interop-to-goose is refused (zero-delegation guard)
# ---------------------------------------------------------------------------

def _validate(argv):
    from agentteams.cli.parser import _build_parser, _validate_option_combinations
    parser = _build_parser()
    _validate_option_combinations(parser, parser.parse_args(argv))


def test_interop_to_goose_is_refused(tmp_path):
    err = io.StringIO()
    with pytest.raises(SystemExit) as exc, redirect_stderr(err):
        _validate(["--interop-from", str(tmp_path), "--framework", "goose",
                   "--output", str(tmp_path / "o")])
    assert exc.value.code == 2
    assert "interop-from with --framework goose is not supported" in err.getvalue()


def test_convert_to_goose_is_not_refused(tmp_path):
    # convert-to-goose IS supported → validation must NOT raise.
    _validate(["--convert-from", str(tmp_path), "--framework", "goose",
               "--output", str(tmp_path / "o")])


def test_interop_to_other_frameworks_still_allowed(tmp_path):
    # the refusal is goose-specific; interop to claude must still validate cleanly.
    _validate(["--interop-from", str(tmp_path), "--framework", "claude",
               "--output", str(tmp_path / "o")])


# ---------------------------------------------------------------------------
# CLI normalize_output_path regression: --convert-from + --output <root>
# ---------------------------------------------------------------------------

def test_convert_to_goose_via_cli_routes_recipes_to_dot_goose(copilot_source, tmp_path):
    """--convert-from --framework goose --output <root> must place recipes in .goose/recipes/.

    Regression: commands._run_convert previously assigned target_dir = output directly
    (bypassing normalize_output_path), placing recipe YAML files at the project root.
    """
    proj_root = tmp_path / "proj"
    proj_root.mkdir()
    rc = build_team.main([
        "--convert-from", str(copilot_source),
        "--framework", "goose",
        "--output", str(proj_root),
        "--yes",
    ])
    assert rc == 0
    recipes_dir = proj_root / ".goose" / "recipes"
    assert recipes_dir.is_dir(), ".goose/recipes/ must be created by normalize_output_path"
    yaml_files = list(recipes_dir.glob("*.yaml"))
    assert yaml_files, "recipe YAML files must land inside .goose/recipes/, not at project root"
    root_yaml = list(proj_root.glob("*.yaml"))
    assert root_yaml == [], f"recipe YAML must NOT appear at project root: {root_yaml}"
