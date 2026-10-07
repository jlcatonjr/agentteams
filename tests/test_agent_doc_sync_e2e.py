"""End-to-end: learned blocks survive REAL regeneration of every framework (review BLOCKER).

Generated goose recipes carry no AGENTTEAMS fence, so ``--update --merge`` full-replaces them
(``fences._is_machine_managed_merge_overwrite_path``); before the carry fix that deleted the goose
learned block and the sync then excluded the copy forever. This generates real claude, goose and
copilot-vscode teams (no fixture fences), adds a block, syncs, regenerates each framework with
``--update --merge``, and checks the block survives everywhere and a v2 edit still propagates.
"""

from __future__ import annotations

import hashlib
import hmac
import shutil
import sys
from pathlib import Path

import pytest

import build_team
from agentteams import agent_doc_sync as ads
from agentteams import emit
from agentteams import learned_blocks as lb

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "examples" / "research-project" / "brief.json"
_KEY = "agent-doc-sync-e2e-waiver-key"
DIRS = {"claude": ".claude/agents", "goose": ".goose/recipes", "copilot-vscode": ".github/agents",
        "codex": ".codex/agents"}
SLUG = "agent-updater"

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not BRIEF.is_file(),
                                reason="POSIX + example brief")


def _seed_offline_waiver(agents_dir: Path) -> None:
    refs = agents_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    fields = ["waiver-GSR", "security-intel-freshness", "2099-01-01T00:00:00Z", "20", "0",
              "security", "GSR-1", "TEST", "verified"]
    sig = hmac.new(_KEY.encode(), "|".join(fields).encode(), hashlib.sha256).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        "2026-05-03T00:00:00Z," + ",".join(fields) + f",{sig}\n", encoding="utf-8")


def _gen(proj: Path, fw: str, *extra: str) -> int:
    return build_team.main(["--description", str(BRIEF), "--project", str(proj), "--framework", fw,
                            "--yes", "--no-scan", "--security-offline", *extra])


@pytest.fixture(scope="module")
def _generated(tmp_path_factory) -> Path:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", _KEY)
        proj = tmp_path_factory.mktemp("doc-sync-e2e") / "proj"
        proj.mkdir()
        for fw, d in DIRS.items():
            _seed_offline_waiver(proj / d)
            assert _gen(proj, fw) == 0, fw
    return proj


@pytest.fixture
def proj(_generated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dst = tmp_path / "proj"
    shutil.copytree(_generated, dst, symlinks=True)
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", _KEY)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(ads, "_stdio_is_tty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    return dst


def _paths(proj: Path) -> dict[str, tuple[Path, str]]:
    return {
        "github": (proj / ".github/agents" / f"{SLUG}.agent.md", lb.MARKDOWN),
        "claude": (proj / ".claude/agents" / f"{SLUG}.md", lb.MARKDOWN),
        "goose": (proj / ".goose/recipes" / f"{SLUG}.yaml", lb.RECIPE),
        "codex": (proj / ".codex/agents" / f"{SLUG}.toml", lb.TOML),
    }


def _blocks(proj: Path) -> dict[str, str | None]:
    out = {}
    for fw, (path, kind) in _paths(proj).items():
        block = lb.parse(path.read_text(encoding="utf-8"), kind).block
        out[fw] = block.content if block else None
    return out


V1 = "- prefer pytest -x for quick iteration\n"
V2 = V1 + "- the docs build needs pandoc 3\n"


def test_real_recipes_have_no_fence_so_merge_full_replaces_them(_generated: Path) -> None:
    from agentteams.fences import _is_machine_managed_merge_overwrite_path

    recipe = (_generated / ".goose/recipes" / f"{SLUG}.yaml").read_text(encoding="utf-8")
    assert _is_machine_managed_merge_overwrite_path(f"{SLUG}.yaml", recipe)


def test_block_survives_every_framework_update_merge_and_v2_propagates(proj: Path) -> None:
    gh = _paths(proj)["github"][0]
    gh.write_text(gh.read_text(encoding="utf-8") + "\n" + lb.render_block(V1, ""), encoding="utf-8")
    rep = ads.sync_agent_docs(proj, apply=True, include_claude=True)
    assert rep.exit_code == 0, rep.lines
    assert _blocks(proj) == {"github": V1, "claude": V1, "goose": V1, "codex": V1}

    for fw in DIRS:
        assert _gen(proj, fw, "--update", "--merge") == 0, fw
        assert _blocks(proj) == {"github": V1, "claude": V1, "goose": V1, "codex": V1}, fw
    # Each file still holds exactly one block, and the regenerated recipes are valid.
    from agentteams.frameworks.goose_recipe_validate import _validate_recipe_yaml

    for recipe in (proj / ".goose/recipes").glob("*.yaml"):
        assert _validate_recipe_yaml(recipe.read_text(encoding="utf-8")) == [], recipe.name
    rep = ads.sync_agent_docs(proj, apply=True)
    assert rep.exit_code == 0 and not rep.removed and not rep.written, rep.lines

    goose = _paths(proj)["goose"][0]
    text = goose.read_text(encoding="utf-8")
    goose.write_text(lb.compose(text, lb.parse(text, lb.RECIPE), V2), encoding="utf-8")
    rep = ads.sync_agent_docs(proj, apply=True, include_claude=True)
    assert rep.exit_code == 0, rep.lines
    assert _blocks(proj) == {"github": V2, "claude": V2, "goose": V2, "codex": V2}


def test_dry_run_update_previews_without_dropping(proj: Path) -> None:
    gh = _paths(proj)["github"][0]
    gh.write_text(gh.read_text(encoding="utf-8") + "\n" + lb.render_block(V1, ""), encoding="utf-8")
    ads.sync_agent_docs(proj, apply=True)
    before = _paths(proj)["goose"][0].read_bytes()
    assert _gen(proj, "goose", "--update", "--merge", "--dry-run") == 0
    assert _paths(proj)["goose"][0].read_bytes() == before


@pytest.mark.parametrize("mode", ["overwrite", "merge"])
def test_emit_overwrite_and_full_replace_carry_the_block(proj: Path, mode: str) -> None:
    recipes = proj / ".goose/recipes"
    target = recipes / f"{SLUG}.yaml"
    fresh = target.read_text(encoding="utf-8")
    target.write_text(lb.compose(fresh, lb.parse(fresh, lb.RECIPE), V1), encoding="utf-8")
    res = emit.emit_all([(f"{SLUG}.yaml", fresh)], output_dir=recipes, yes=True,
                        overwrite=(mode == "overwrite"), merge=(mode == "merge"))
    assert res.success, res.errors
    assert lb.parse(target.read_text(encoding="utf-8"), lb.RECIPE).block.content == V1
