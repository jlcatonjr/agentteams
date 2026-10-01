"""Follow-up #15: the goose orchestrator's top-level `sub_recipes` key is owned structurally.

`--update --merge` fence-merges `orchestrator.yaml` (its instructions carry template fences), and
`sub_recipes` sits outside every fence — so before this a stale on-disk list (delegating to a
since-removed tool agent, missing a new roster member) was preserved forever. The generic fence
engine cannot own the key: a new fence lands inside the `instructions: |` block scalar, and a
column-0 line there corrupts the YAML. `goose_recipe_merge.reconcile_sub_recipes` owns it by span.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import shutil
from pathlib import Path

import pytest

import build_team
from agentteams.frameworks.goose_recipe_merge import (
    SUB_RECIPES_MANAGED_COMMENT,
    reconcile_sub_recipes,
)
from agentteams.frameworks.goose_recipe_read import _recipe_block_scalar
from agentteams.frameworks.goose_recipe_validate import _validate_recipe_yaml

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "examples" / "research-project" / "brief.json"

_HEAD = 'version: "1.0.0"\ntitle: "Orchestrator"\ninstructions: |\n  Route work.\n  <!-- AGENTTEAMS:BEGIN x v=1 -->\n  body\n  <!-- AGENTTEAMS:END x -->\nextensions:\n  - type: platform\n    name: summon\n'


def _recipe(entries: list[tuple[str, str]], *, comment: bool = True) -> str:
    if not entries:
        return _HEAD
    lines = [SUB_RECIPES_MANAGED_COMMENT] if comment else []
    lines.append("sub_recipes:")
    for name, path in entries:
        lines += [f'  - name: "{name}"', f'    path: "{path}"']
    return _HEAD + "\n".join(lines) + "\n"


FRESH = _recipe([("navigator", "./navigator.yaml"), ("research_analyst", "./research-analyst.yaml")])
STALE = _recipe(
    [("navigator", "./navigator.yaml"), ("tool_pandoc", "./tool-pandoc.yaml")], comment=False
)


# --------------------------------------------------------------------------------------
# Unit: the span reconcile
# --------------------------------------------------------------------------------------


def test_stale_unfenced_block_is_replaced_and_notices_list_the_changes() -> None:
    text, notices = reconcile_sub_recipes(FRESH, STALE)
    assert text == FRESH
    assert any("added research_analyst" in n for n in notices), notices
    assert any("removed tool_pandoc" in n for n in notices), notices


def test_reconcile_is_idempotent() -> None:
    once, _ = reconcile_sub_recipes(FRESH, STALE)
    twice, notices = reconcile_sub_recipes(FRESH, once)
    assert twice == once and notices == []


def test_instructions_are_byte_identical() -> None:
    disk = STALE.replace("  Route work.\n", "  Route work. (hand edit)\n")
    text, _ = reconcile_sub_recipes(FRESH, disk)
    assert _recipe_block_scalar(text, "instructions") == _recipe_block_scalar(disk, "instructions")


def test_absent_key_is_appended_and_dropped_key_is_removed() -> None:
    appended, _ = reconcile_sub_recipes(FRESH, _HEAD)
    assert appended == FRESH
    removed, notices = reconcile_sub_recipes(_HEAD, STALE)
    assert "sub_recipes" not in removed and removed == _HEAD
    assert any("removed navigator, tool_pandoc" in n for n in notices), notices


def test_key_followed_by_another_top_level_key_keeps_it() -> None:
    disk = STALE + 'retry:\n  max_retries: 1\n  timeout_seconds: 5\n  checks:\n    - type: "shell"\n      command: "true"\n'
    text, _ = reconcile_sub_recipes(FRESH, disk)
    assert text.endswith('command: "true"\n') and "tool_pandoc" not in text
    assert text.count("sub_recipes:") == 1


@pytest.mark.parametrize(
    "shape",
    [
        "sub_recipes: []\n",
        "sub_recipes:  # hand-curated\n  - name: \"a\"\n    path: \"./a.yaml\"\n",
        "sub_recipes:\n  - name: \"a\"  # keep\n    path: \"./a.yaml\"\n",
        "sub_recipes:\n  - name: \"a\"\n    path: \"./a.yaml\"\nsub_recipes:\n  - name: \"b\"\n",
    ],
)
def test_unrecognised_shapes_are_left_alone_with_a_notice(shape: str) -> None:
    disk = _HEAD + shape
    text, notices = reconcile_sub_recipes(FRESH, disk)
    assert text == disk
    assert notices and "left unchanged" in notices[0]


def test_non_recipe_files_are_untouched() -> None:
    other = "sub_recipes:\n  - name: \"a\"\n"
    assert reconcile_sub_recipes(FRESH, other) == (other, [])


def test_an_edit_that_would_break_validation_is_rolled_back(monkeypatch) -> None:
    import agentteams.frameworks.goose_recipe_merge as grm

    monkeypatch.setattr(
        grm, "_validate_recipe_yaml",
        lambda text, recipes_dir=None: ["boom"] if "research_analyst" in text else [],
    )
    text, notices = grm.reconcile_sub_recipes(FRESH, STALE)
    assert text == STALE
    assert notices and "rolled back" in notices[0]


# --------------------------------------------------------------------------------------
# End to end: --update --merge on a generated goose team
# --------------------------------------------------------------------------------------


_WAIVER_KEY = "goose-sub-recipes-waiver-key"


def _seed_offline_waiver(output_dir: Path) -> None:
    """Signed `security-intel-freshness` waiver so `--security-offline` runs without live intel."""
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    fields = ["waiver-GSR", "security-intel-freshness", "2099-01-01T00:00:00Z", "20", "0",
              "security", "GSR-1", "TEST", "verified"]
    sig = hmac.new(_WAIVER_KEY.encode(), "|".join(fields).encode(), hashlib.sha256).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        "2026-05-03T00:00:00Z," + ",".join(fields) + f",{sig}\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def _env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", _WAIVER_KEY)
        yield


@pytest.fixture(scope="module")
def _generated(tmp_path_factory, _env) -> Path:
    if not BRIEF.is_file():
        pytest.skip("research-project brief not found")
    proj = tmp_path_factory.mktemp("goose-gen") / "proj"
    proj.mkdir()
    _seed_offline_waiver(proj / ".goose" / "recipes")
    assert build_team.main([
        "--description", str(BRIEF), "--project", str(proj), "--framework", "goose",
        "--yes", "--no-scan", "--security-offline",
    ]) == 0
    return proj


def _update(proj: Path) -> int:
    return build_team.main([
        "--description", str(BRIEF), "--project", str(proj), "--framework", "goose",
        "--update", "--merge", "--yes", "--no-scan", "--security-offline",
    ])


def test_update_merge_reconciles_a_stale_sub_recipes_block(
    _generated: Path, tmp_path: Path, capsys
) -> None:
    proj = tmp_path / "proj"
    shutil.copytree(_generated, proj, symlinks=True)
    recipes = proj / ".goose" / "recipes"
    orch = recipes / "orchestrator.yaml"
    fresh = orch.read_text(encoding="utf-8")
    names = re.findall(r'^  - name: "([^"]+)"', fresh, re.MULTILINE)
    victim = names[0]
    # Stale on-disk state: unfenced block without the managed comment, delegating to a legacy
    # tool agent and missing a roster member.
    stale = re.sub(
        rf'^  - name: "{victim}"\n    path: "[^"]+"\n(    description: "[^"]*"\n)?',
        '  - name: "tool_pandoc"\n    path: "./tool-pandoc.yaml"\n',
        fresh.replace(SUB_RECIPES_MANAGED_COMMENT + "\n", ""),
        flags=re.MULTILINE,
    )
    assert stale != fresh
    orch.write_text(stale, encoding="utf-8")
    (recipes / "tool-pandoc.yaml").write_text(
        'version: "1.0.0"\ntitle: "Pandoc"\ninstructions: |\n  legacy\n', encoding="utf-8"
    )

    assert _update(proj) == 0
    out = capsys.readouterr()
    merged = orch.read_text(encoding="utf-8")

    assert "tool_pandoc" not in merged, "a tool (a doc, not an agent) is still delegated to"
    assert f'name: "{victim}"' in merged
    assert merged.count("sub_recipes:") == 1 and SUB_RECIPES_MANAGED_COMMENT in merged
    assert _validate_recipe_yaml(merged, recipes) == [], "reconciled recipe must validate"
    assert _recipe_block_scalar(merged, "instructions") == _recipe_block_scalar(stale, "instructions")
    combined = out.out + out.err
    assert f"sub_recipes: added {victim}" in combined
    assert "removed tool_pandoc" in combined

    # Idempotent: a second run leaves the recipe byte-identical and reports nothing new.
    assert _update(proj) == 0
    again = capsys.readouterr()
    assert orch.read_text(encoding="utf-8") == merged
    assert "sub_recipes: added" not in again.out + again.err


def test_column0_comment_inside_the_list_is_unrecognised() -> None:
    """@adversarial P3-3: a column-0 comment must not strand the entries after it."""
    interrupted = STALE.replace('  - name: "tool_pandoc"', '# hand note\n  - name: "tool_pandoc"')
    text, notices = reconcile_sub_recipes(FRESH, interrupted)
    assert text == interrupted and any("not recognised" in n for n in notices), notices


def test_trailing_column0_comment_after_the_list_is_fine() -> None:
    text, _ = reconcile_sub_recipes(FRESH, STALE + "# trailing note\n")
    assert "tool_pandoc" not in text and text.endswith("# trailing note\n")


def test_structural_invariant_rolls_back(monkeypatch) -> None:
    from agentteams.frameworks import goose_recipe_merge as grm

    real = grm._span
    calls = {"n": 0}

    def flaky(lines):
        calls["n"] += 1
        return None if calls["n"] == 3 else real(lines)  # the post-edit re-parse "loses" the span

    monkeypatch.setattr(grm, "_span", flaky)
    text, notices = grm.reconcile_sub_recipes(FRESH, STALE)
    assert text == STALE and any("structural invariant" in n for n in notices), notices
