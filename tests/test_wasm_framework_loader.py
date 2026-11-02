"""The rendered WASM team's control-plane lock and loader (WASM plan P08 §4.4, ``wasm_agent/team.py``).

Every refusal names the path; the fix is always a re-render. Tests that edit a file and then re-write the lock
model a writer that also controls the control plane: the structural checks must still refuse.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agentteams import wasm_exec as W
from agentteams.wasm_agent import team as T
from agentteams.wasm_agent.guest_source import GUEST_SHA256
from tests._wasm_framework_helpers import project, render, write_brief


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = project(tmp_path_factory.mktemp("wasmload"))
    assert render(root, write_brief(root)) == 0
    return root


@pytest.fixture
def team_root(pristine: Path, tmp_path: Path) -> Path:
    root = Path(os.path.realpath(tmp_path)) / "proj"
    shutil.copytree(pristine, root, symlinks=True)
    return root


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, (rel, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_lock_round_trip_and_spec(team_root: Path) -> None:
    team = T.load_team(team_root)
    assert "orchestrator" in team.agents and "SETUP-REQUIRED" not in team.agents
    lock = json.loads((team_root / T.LOCK_REL).read_text())
    assert lock["guest_sha256"] == GUEST_SHA256 and set(lock["agents"]) == set(team.agents)
    spec = team.agent_spec("primary-producer", environ={})
    assert spec.slug == "primary-producer" and spec.provider == "stub" and spec.model == "stub"
    assert spec.tools == frozenset({"read_file", "list_dir", "search_files", "write_file"})
    assert [(m.guest, m.mode) for m in spec.grant.mounts] == [("/project/docs", "ro"), ("/scratch", "rw")]
    assert spec.grant.mounts[0].host == str(team_root / "docs")
    assert spec.system_prompt.startswith("<!-- TEAM.md") and "## Your WASM runtime" in spec.system_prompt
    assert "/project/docs (read-only)" in spec.system_prompt and str(team_root) not in spec.system_prompt
    assert len(spec.spec_sha256) == 64 and spec.max_tokens == 8192
    assert team.budget().max_steps == 40                          # the brief's wasm_runtime.budget
    orch = team.agent_spec("orchestrator", environ={})
    assert orch.tools == frozenset({"run_code", "delegate"}) and orch.grant == W.COMPUTE_ONLY
    assert "navigator" in orch.delegates


@pytest.mark.parametrize("rel, old, new", [
    (".agentteams-wasm/agents/navigator.md", "model: cheap", "model: primary"),
    (T.TEAM_MD_REL, "Agent Team", "Agent Teem"),
    (T.GRANTS_REL, '"timeout_s": 120', '"timeout_s": 121'),
    (T.RUNTIME_REL, '"provider": "stub"', '"provider": "anthropic"'),
    (T.README_REL, "WASM agent team", "WASM agent teem"),
])
def test_an_edited_file_is_refused_with_its_path(team_root: Path, rel: str, old: str, new: str) -> None:
    _edit(team_root, rel, old, new)
    with pytest.raises(T.TeamError, match=f"{rel} \\(changed\\)"):
        T.load_team(team_root)


def test_a_planted_agent_is_refused(team_root: Path) -> None:
    shutil.copy(team_root / ".agentteams-wasm/agents/navigator.md", team_root / ".agentteams-wasm/agents/evil.md")
    with pytest.raises(T.TeamError, match="evil.md \\(not in the lock\\)"):
        T.load_team(team_root)


def test_a_missing_agent_is_refused(team_root: Path) -> None:
    (team_root / ".agentteams-wasm/agents/navigator.md").unlink()
    with pytest.raises(T.TeamError, match="navigator.md"):
        T.load_team(team_root)


def test_setup_required_is_never_loaded_or_locked(team_root: Path) -> None:
    (team_root / ".agentteams-wasm/agents/SETUP-REQUIRED.md").write_text("anything\n")
    assert "SETUP-REQUIRED" not in T.load_team(team_root).agents


def test_a_symlinked_file_is_refused(team_root: Path) -> None:
    agent = team_root / ".agentteams-wasm/agents/navigator.md"
    real = team_root / "navigator.real.md"
    agent.rename(real)
    agent.symlink_to(real)
    with pytest.raises(T.TeamError, match="navigator.md"):
        T.load_team(team_root)


def test_a_symlinked_team_dir_is_refused(team_root: Path, tmp_path: Path) -> None:
    moved = tmp_path / "elsewhere"
    (team_root / ".agentteams-wasm").rename(moved)
    (team_root / ".agentteams-wasm").symlink_to(moved)
    with pytest.raises(T.TeamError, match="not a real directory"):
        T.load_team(team_root)


def test_no_lock_refuses(team_root: Path) -> None:
    (team_root / T.LOCK_REL).unlink()
    with pytest.raises(T.TeamError, match="render the team with --framework wasm first"):
        T.load_team(team_root)


def test_an_unknown_front_matter_key_is_refused_even_relocked(team_root: Path) -> None:
    _edit(team_root, ".agentteams-wasm/agents/navigator.md", "model: cheap", "permissionMode: plan")
    T.write_lock(team_root)
    with pytest.raises(T.TeamError, match="expected front-matter key 'model'"):
        T.load_team(team_root)


def test_a_model_id_in_front_matter_is_refused(team_root: Path) -> None:
    _edit(team_root, ".agentteams-wasm/agents/navigator.md", "model: cheap", "model: some-model-1")
    T.write_lock(team_root)
    with pytest.raises(T.TeamError, match="tier"):
        T.load_team(team_root)


def test_tools_beyond_the_grant_are_refused_even_relocked(team_root: Path) -> None:
    _edit(team_root, ".agentteams-wasm/agents/navigator.md", "tools: [run_code]", "tools: [read_file, run_code]")
    T.write_lock(team_root)
    with pytest.raises(T.TeamError, match="exceed what its grant can serve"):
        T.load_team(team_root)


@pytest.mark.parametrize("mount", [
    {"host": "src", "guest": "/project/docs", "mode": "ro"},                     # host/guest mismatch
    {"host": "docs", "guest": "/project/docs", "mode": "rw"},                    # project files never writable
    {"host": ".agentteams/wasm/scratch/navigator", "guest": "/scratch", "mode": "rw"},  # another agent's scratch
    {"host": "/etc", "guest": "/project/etc", "mode": "ro"},
])
def test_a_non_canonical_grant_mount_is_refused_even_relocked(team_root: Path, mount: dict) -> None:
    doc = json.loads((team_root / T.GRANTS_REL).read_text())
    doc["agents"]["primary-producer"]["mounts"] = [mount]
    (team_root / T.GRANTS_REL).write_text(T.dumps(doc))
    T.write_lock(team_root)
    with pytest.raises(T.TeamError, match="primary-producer"):
        T.load_team(team_root)


def test_a_delegate_outside_the_team_is_refused(team_root: Path) -> None:
    _edit(team_root, ".agentteams-wasm/agents/orchestrator.md", "delegates: [", "delegates: [ghost, ")
    T.write_lock(team_root)
    with pytest.raises(T.TeamError, match="ghost"):
        T.load_team(team_root)


def test_update_rerender_keeps_the_lock_valid(tmp_path: Path) -> None:
    # Compute-only (no wasm_sandbox block): a widened grant on --update needs the operator's signed decision once the
    # first render made the workspace governed (P01/P15 behaviour, the same for every framework).
    root = project(tmp_path)
    brief = write_brief(root, wasm_sandbox=None)
    assert render(root, brief) == 0
    before = (root / T.LOCK_REL).read_bytes()
    assert render(root, brief, "--update") == 0
    T.load_team(root)
    assert (root / T.LOCK_REL).read_bytes() == before


# --- models and runtime config -----------------------------------------------------------------------------------

def test_models_are_null_without_the_brief_and_refuse_for_a_real_provider(team_root: Path) -> None:
    team = T.load_team(team_root)
    assert team.runtime["models"] == {"primary": None, "cheap": None}
    with pytest.raises(T.TeamError, match="no model configured for tier 'primary': set wasm_runtime.models.primary "
                                          "in the brief or AGENTTEAMS_WASM_MODEL_PRIMARY"):
        team.agent_spec("orchestrator", provider="anthropic", environ={})


def test_env_overrides_the_tier_model_at_load_time(team_root: Path) -> None:
    spec = T.load_team(team_root).agent_spec("navigator", provider="openai",
                                            environ={"AGENTTEAMS_WASM_MODEL_CHEAP": "env-model"})
    assert spec.model == "env-model" and spec.provider == "openai"


def test_unknown_provider_and_unknown_agent_refuse(team_root: Path) -> None:
    team = T.load_team(team_root)
    with pytest.raises(T.TeamError, match="unknown provider"):
        team.agent_spec("navigator", provider="nope", environ={})
    with pytest.raises(T.TeamError, match="no agent 'ghost'"):
        team.agent_spec("ghost", environ={})


def test_brief_models_are_carried(tmp_path: Path) -> None:
    root = project(tmp_path)
    brief = write_brief(root, wasm_runtime={"provider": "openai", "models": {"primary": "brief-model"}})
    assert render(root, brief) == 0
    runtime = json.loads((root / T.RUNTIME_REL).read_text())
    assert runtime["provider"] == "openai" and runtime["models"] == {"primary": "brief-model", "cheap": None}
    assert T.load_team(root).agent_spec("orchestrator", environ={}).model == "brief-model"


@pytest.mark.parametrize("block, match", [
    ({"provider": "nope"}, "provider"),
    ({"api_key": "x"}, "unknown key"),
    ({"provider": "openai", "base_url": "http://example.com/v1"}, "base_url"),
    ({"provider": "openai", "base_url": "https://u:p@example.com/v1"}, "base_url"),
    ({"models": {"primary": "has space"}}, "models.primary"),
    ({"budget": {"max_steps": -1}}, "budget"),
    ({"prices_file": "../prices.json"}, "prices_file"),
])
def test_a_malformed_runtime_block_refuses(block: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        T.normalize_wasm_runtime(block)


def test_check_runtime_refuses_a_hand_written_key() -> None:
    data = T.runtime_record({}, os_on=True)
    assert T.check_runtime(dict(data)) == data
    with pytest.raises(T.TeamError):
        T.check_runtime({**data, "api_key": "x"})
