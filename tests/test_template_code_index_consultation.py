"""F-CODEIDX copilot-surfacing guard.

The code index must be surfaced to the PRIMARY agent team (every framework —
Copilot has no skills concept, only agent templates + references), not merely as
a Claude bridge skill. These tests assert that the code-index consultation lives
in the agent templates the `--update` render emits into `.github/agents/` (or
`.claude/agents/`), mirroring how the memory index is surfaced.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NAVIGATOR = REPO_ROOT / "agentteams/templates/universal/navigator.template.md"
RETRIEVAL_INTEGRATOR = REPO_ROOT / "agentteams/templates/domain/retrieval-integrator.template.md"

_BEGIN = re.compile(r"<!--\s*AGENTTEAMS:BEGIN\s+code_index_consultation\b")
_END = re.compile(r"<!--\s*AGENTTEAMS:END\s+code_index_consultation\b")


def test_navigator_teaches_query_code():
    text = NAVIGATOR.read_text(encoding="utf-8")
    assert "--query-code" in text, "navigator must route code/API queries to the code index"
    assert "--code-kind" in text
    # The label vocabulary must be present so agents can filter by kind.
    assert "[local-script]" in text and "[api-module]" in text and "[api-doc]" in text


def test_retrieval_integrator_has_balanced_code_index_fence():
    text = RETRIEVAL_INTEGRATOR.read_text(encoding="utf-8")
    assert "--query-code" in text
    assert len(_BEGIN.findall(text)) == 1, "exactly one code_index_consultation BEGIN"
    assert len(_END.findall(text)) == 1, "exactly one code_index_consultation END"
    # BEGIN precedes END.
    assert _BEGIN.search(text).start() < _END.search(text).start()


def test_untrusted_api_docstring_caveat_present():
    # api-* content is third-party data; both templates must warn against treating
    # retrieved docstrings as instructions (docstring prompt-injection).
    for path in (NAVIGATOR, RETRIEVAL_INTEGRATOR):
        text = path.read_text(encoding="utf-8").lower()
        assert "instructions" in text and ("not instructions" in text or "never as instructions" in text
                or "not as instructions" in text or "data, not instructions" in text), (
            f"{path.name} must carry the untrusted-api-docstring caveat"
        )


def test_code_index_surfaced_in_rendered_copilot_team(tmp_path):
    """End-to-end: a generated copilot team's agent files must contain --query-code.

    This is the real guard — it proves the code index reaches the PRIMARY
    (Copilot) infrastructure, not only the Claude bridge skill.
    """
    import build_team

    desc = tmp_path / "brief.json"
    desc.write_text(
        '{"project_name": "CodeIdxSurfaceTest", "project_goal": '
        '"A test project to verify code-index consultation renders into the copilot team.", '
        '"deliverables": ["scripts"], "framework": "copilot-vscode", '
        '"existing_project_path": "' + str(tmp_path).replace("\\", "/") + '"}',
        encoding="utf-8",
    )
    out = tmp_path / ".github" / "agents"
    rc = build_team.main([
        "--description", str(desc), "--output", str(out), "--yes", "--no-scan",
        "--no-git-hooks",
    ])
    assert rc == 0, f"generate failed rc={rc}"
    agent_files = list(out.glob("*.agent.md"))
    assert agent_files, "no agent files rendered"
    corpus = "\n".join(f.read_text(encoding="utf-8") for f in agent_files)
    assert "--query-code" in corpus, (
        "the rendered copilot agent team must surface the code index (--query-code) "
        "in at least one agent (navigator/retrieval-integrator) — not only the Claude skill"
    )
