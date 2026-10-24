"""Operator directories stay out of every agent sandbox's reach (vulnscan test campaign M1, M2, I15).

Goose, Codex and Grok sandboxes write only to validated write roots (``_write_roots.validate_write_roots``); Claude
Code's built-in Write/Edit tools ignore the sandbox, so it also gets ``Edit(...)`` deny rules. Both must cover the
operator config directory (keys, grants, vulnscan config) and the operator state directory (vulnscan ledger, spool,
lock). The integrity pin set must cover every vulnscan module and every file of the packaged server kit.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import integrity
from agentteams.frameworks import _sandbox_emit, _write_roots

REPO = Path(__file__).resolve().parent.parent

OPERATOR_ROOTS = [
    "~/.config/agentteams", "~/.config/agentteams/vulnscan", "~/.config/agentteams/server-kit",
    "~/.local/state/agentteams", "~/.local/state/agentteams/vulnscan", "~/.local/state/agentteams/vulnscan/spool",
    "~/.local/state", "~/.local",  # ancestors
]


@pytest.mark.parametrize("platform", ["darwin", "linux"])
@pytest.mark.parametrize("root", OPERATOR_ROOTS)
def test_operator_dirs_are_refused_write_roots(root: str, platform: str) -> None:
    """A write root at, inside or above an operator directory is refused on every platform."""
    assert _write_roots.root_problem(root, "/srv/project", platform=platform), root
    with pytest.raises(ValueError, match="refused sandbox write root"):
        _write_roots.validate_write_roots([root], project_root="/srv/project", platform=platform)


def test_case_variant_refused_on_darwin() -> None:
    """darwin's case-insensitive filesystem cannot be used to slip past the state-dir ban."""
    assert _write_roots.root_problem("~/.Local/State/AgentTeams/vulnscan", "/srv/project", platform="darwin")


@pytest.mark.parametrize("root", ["~/.local/state/othertool", "~/.config/othertool", "./build"])
def test_neighbouring_dirs_still_allowed(root: str) -> None:
    """The ban is exact: a sibling of an operator directory remains a valid write root."""
    assert _write_roots.root_problem(root, "/srv/project", platform="linux") is None


MACOS_PERSISTENCE_ROOTS = ["~/Library/LaunchAgents", "~/Library/LaunchAgents/x.plist", "~/Library/Keychains",
                           "~/Library/Keychains/login.keychain-db", "~/Library"]


@pytest.mark.parametrize("platform", ["darwin", "linux"])
@pytest.mark.parametrize("root", MACOS_PERSISTENCE_ROOTS)
def test_launchagents_and_keychains_are_refused(root: str, platform: str) -> None:
    """X5: the daily job's plist lives in ~/Library/LaunchAgents; ~/Library is refused as an ancestor of it."""
    reason = _write_roots.root_problem(root, "/srv/project", platform=platform)
    assert reason and "protected home path" in reason, (root, reason)


def test_library_case_variant_refused_on_darwin() -> None:
    """darwin folds case: ~/library/launchagents is the same directory."""
    assert _write_roots.root_problem("~/library/launchagents", "/srv/project", platform="darwin")


@pytest.mark.parametrize("root", ["~/Library/CloudStorage/x", "~/Library/Caches/tool", "~/Library/LaunchAgentsX"])
def test_other_library_dirs_still_allowed(root: str) -> None:
    """X5 protects two directories, not all of ~/Library (CloudStorage roots are legitimate write roots)."""
    assert _write_roots.root_problem(root, "/srv/project", platform="darwin") is None


@pytest.mark.parametrize("framework", ["claude", "goose", "codex", "grok"])
def test_claude_edit_rules_cover_launchagents(framework: str) -> None:
    """The built-in Write/Edit tools ignore the sandbox, so the LaunchAgents dir also gets Edit deny rules."""
    rules = _sandbox_emit.permission_deny_rules(framework)
    assert "Edit(~/Library/LaunchAgents)" in rules and "Edit(~/Library/LaunchAgents/**)" in rules


def test_write_roots_copies_match_the_emitter_constants() -> None:
    """_write_roots keeps literal copies (no import of the emitter); they must name the same directories."""
    assert _sandbox_emit.OPERATOR_CONFIG_DIR in _write_roots.HOME_PROTECTED
    assert _sandbox_emit.OPERATOR_STATE_DIR in _write_roots.HOME_PROTECTED


def test_state_dir_in_public_alias() -> None:
    """vulnscan's team-dir check reads the public alias, so it refuses the state dir too."""
    from agentteams.vulnscan import agent_run
    assert "~/.local/state/agentteams" in _write_roots.HOME_PROTECTED
    assert agent_run.TEAM_DIR_PROTECTED is _write_roots.HOME_PROTECTED


@pytest.mark.parametrize("framework", ["claude", "goose", "codex", "grok"])
def test_claude_edit_rules_cover_operator_dirs(framework: str) -> None:
    """Every framework's emitted Claude permission rules deny edits to both operator directories."""
    rules = _sandbox_emit.permission_deny_rules(framework)
    for d in (_sandbox_emit.OPERATOR_CONFIG_DIR, _sandbox_emit.OPERATOR_STATE_DIR):
        assert f"Edit({d})" in rules and f"Edit({d}/**)" in rules, (framework, d)


def test_state_dir_constant_matches_paths_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The protected path is where vulnscan actually keeps its state when no override is set."""
    from agentteams.vulnscan import paths
    monkeypatch.delenv("AGENTTEAMS_VULNSCAN_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = Path(_sandbox_emit.OPERATOR_STATE_DIR.replace("~", str(tmp_path), 1))
    assert paths.state_dir().parent == expected


def test_pin_set_covers_vulnscan_and_server_kit() -> None:
    """M1: every vulnscan module and every executable/data file of the packaged kit and agent image is pinned."""
    pinned = set(integrity.ENFORCEMENT_MODULES)
    expected = {p.relative_to(REPO).as_posix() for p in (REPO / "agentteams/vulnscan").glob("*.py")}
    kit = REPO / "agentteams/data/server_kit"
    expected |= {p.relative_to(REPO).as_posix() for p in kit.iterdir()
                 if p.is_file() and not p.name.endswith(".example")}
    expected |= {"agentteams/data/vulnscan_agent/Dockerfile"}
    missing = sorted(expected - pinned)
    assert not missing, f"unpinned vulnscan files: {missing}"
