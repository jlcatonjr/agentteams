"""#609: the orchestrator's Update Compatibility Source Pack names the files the team's own framework emits.

The fence listed the Copilot paths (`.github/copilot-instructions.md`, `.github/agents/agent-updater.agent.md`,
`.github/agents/references/...`) for every framework, so a Claude, Goose, Codex or agents-md orchestrator's update
pre-flight read files its project does not have. The paths now come from each adapter, and the fence is
template-owned so an existing team's old list is replaced on `--update --merge` rather than pinned by
preserve-on-shrink.
"""

from __future__ import annotations

import contextlib
import io
import re
import warnings
from pathlib import Path

import pytest

import build_team
from agentteams.cli import security_gate
from agentteams.fences import _merge_fenced_content
from agentteams.frameworks.canonical_paths import canonical_path_placeholders
from agentteams.frameworks.registry import FRAMEWORK_IDS, agents_dir_for

_ROOT = Path(__file__).resolve().parents[1]
_SID = "update_compatibility_source_pack"
_ITEM = re.compile(r"^\s*[123]\. `([^`]+)` — ", re.M)


def _render(fw: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)
    agents = agents_dir_for(fw, root)
    args = ["--description", str(_ROOT / "examples" / "software-project" / "brief.json"), "--framework", fw,
            "--output", str(agents), "--yes", "--no-scan", "--security-offline"]
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        assert build_team.main(args) == 0
    return next(p for p in agents.iterdir() if p.name.startswith("orchestrator."))


@pytest.mark.parametrize("fw", FRAMEWORK_IDS)
def test_every_canonical_file_exists_in_the_render(fw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = _render(fw, tmp_path, monkeypatch)
    listed = _ITEM.findall(orchestrator.read_text(encoding="utf-8"))
    assert len(listed) == 3, listed
    missing = [p for p in listed if not (tmp_path / p).is_file()]
    assert not missing, f"{fw}: the source pack names files the render does not have: {missing}"


def test_the_paths_come_from_the_adapter() -> None:
    assert canonical_path_placeholders("copilot-vscode") == {
        "TEAM_INSTRUCTIONS_FILE": ".github/copilot-instructions.md", "TEAM_AGENTS_DIR": ".github/agents",
        "AGENT_FILE_SUFFIX": ".agent.md", "CONFLICT_LOG_PATH": ".github/agents/references/conflict-log.csv"}
    assert canonical_path_placeholders("claude")["TEAM_INSTRUCTIONS_FILE"] == ".claude/CLAUDE.md"
    assert canonical_path_placeholders("codex")["AGENT_FILE_SUFFIX"] == ".toml"
    assert canonical_path_placeholders("goose")["TEAM_AGENTS_DIR"] == ".goose/recipes"
    # An unknown id keeps the Copilot layout, as conflict_log_path_for always did.
    assert canonical_path_placeholders("no-such-framework") == canonical_path_placeholders("copilot-vscode")


def _fence(items: list[str]) -> str:
    body = "".join(f"{n}. `{p}` — what it holds\n" for n, p in enumerate(items, 1))
    return f"# Orchestrator\n\n<!-- AGENTTEAMS:BEGIN {_SID} v=2 -->\n### Update Compatibility Source Pack\n\n" \
           f"{body}<!-- AGENTTEAMS:END {_SID} -->\n"


def test_merge_takes_new_list_for_existing_claude_team() -> None:
    old = _fence([".github/copilot-instructions.md", ".github/agents/agent-updater.agent.md",
                  ".github/agents/references/github-workflows-merge.reference.md", "SETUP-REQUIRED.md"])
    new = _fence([".claude/CLAUDE.md", ".claude/agents/agent-updater.md",
                  ".claude/agents/references/github-workflows-merge.reference.md", "SETUP-REQUIRED.md"])
    mr = _merge_fenced_content(new, old, preserve_on_shrink=True, rel_path=".claude/agents/orchestrator.md")
    assert _SID not in mr.sections_preserved
    assert ".claude/agents/agent-updater.md" in mr.merged_content
    assert ".github/agents/agent-updater.agent.md" not in mr.merged_content


def test_copilot_render_is_unchanged() -> None:
    # Negative control: the Copilot paths are the ones the template used to hard-code, so its goldens hold them.
    for golden in sorted(_ROOT.glob("examples/*/expected/orchestrator.agent.md")):
        listed = _ITEM.findall(golden.read_text(encoding="utf-8"))
        assert listed == [".github/copilot-instructions.md", ".github/agents/agent-updater.agent.md",
                          ".github/agents/references/github-workflows-merge.reference.md"], golden
