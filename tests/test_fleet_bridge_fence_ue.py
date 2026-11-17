"""The fleet's USER-EDITABLE detector treats an AGENTTEAMS-BRIDGE fence as generated text (rc2 soak, 2026-11-17).

In the rc2 locked soak, six goose-bridge / codex-direct targets each reported one user-editable deletion: the
AGENTS.md install hint moving from `@v1.1.4` to `@v1.2.0-rc.2`. That line sits inside
`<!-- AGENTTEAMS-BRIDGE:BEGIN goose-bridge-entry v=1 -->`, a generated fence, but `_fence_and_useredit_lines` only
knew `AGENTTEAMS:BEGIN`/`END`, so a user region opened earlier in the file ran on through the bridge block.
"""

from __future__ import annotations

from pathlib import Path

from agentteams import fleet
from tests.test_fleet import _git, _init_repo

_RULES = ("## Project-Specific Rules\n\n"
          "> ⚙️ **USER-EDITABLE** — project-specific rules. Preserved across `agentteams --update --merge`.\n\n"
          "- our rule one\n- our rule two\n\n")


def _agents_md(version: str, rules: str = _RULES) -> str:
    return ("# Agents\n\n" + rules +
            "<!-- AGENTTEAMS-BRIDGE:BEGIN goose-bridge-entry v=1 -->\n"
            "Install the research extra:\n"
            f'`pip install "agentteams[research] @ git+https://github.com/jlcatonjr/agentteams.git@{version}"`\n'
            "<!-- AGENTTEAMS-BRIDGE:END goose-bridge-entry -->\n")


def _repo(tmp_path: Path, text: str) -> tuple[Path, str]:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(text, encoding="utf-8")
    _init_repo(repo)
    return repo, _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_a_bridge_fence_line_is_generated_not_user_editable():
    fenced, useredit = fleet._fence_and_useredit_lines(_agents_md("v1.1.4"))
    lines = _agents_md("v1.1.4").splitlines()
    hint = next(i for i, ln in enumerate(lines, 1) if "pip install" in ln)
    rule = next(i for i, ln in enumerate(lines, 1) if "our rule one" in ln)
    assert hint in fenced and hint not in useredit
    assert rule in useredit


def test_the_install_hint_moving_versions_is_not_a_user_deletion(tmp_path: Path):
    repo, ref = _repo(tmp_path, _agents_md("v1.1.4"))
    (repo / "AGENTS.md").write_text(_agents_md("v1.2.0-rc.2"), encoding="utf-8")
    assert fleet._user_editable_deletions(repo, ref, "AGENTS.md") == []


def test_a_real_user_line_deleted_before_the_bridge_fence_is_still_flagged(tmp_path: Path):
    repo, ref = _repo(tmp_path, _agents_md("v1.1.4"))
    (repo / "AGENTS.md").write_text(_agents_md("v1.2.0-rc.2", _RULES.replace("- our rule two\n", "")),
                                    encoding="utf-8")
    dels = fleet._user_editable_deletions(repo, ref, "AGENTS.md")
    assert len(dels) == 1 and "our rule two" in dels[0]
