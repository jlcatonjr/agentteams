"""The cross-repo guard names the project's repository, never its output directory.

``{PRIMARY_OUTPUT_DIR}`` is a directory (or literal glob text such as ``*/outputs/``)
inside the project. Rendering it as "a repository other than `src/`" misdirects the
cross-repo hand-off (OrthodoxLLM triage, PR #192 follow-up).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_MISUSE = re.compile(r"repository other than [`'\"]?\{?PRIMARY_OUTPUT_DIR")


def test_no_template_names_output_dir_as_a_repository() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "agentteams" / "templates").rglob("*.md")
        if _MISUSE.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_git_operations_guard_uses_project_name() -> None:
    text = (ROOT / "agentteams/templates/universal/git-operations.template.md").read_text(encoding="utf-8")
    assert "a repository other than this project's own ({PROJECT_NAME})" in text


def test_rendered_goldens_never_name_a_directory_as_the_repository() -> None:
    for golden in sorted((ROOT / "examples").glob("*/expected/git-operations.agent.md")):
        text = golden.read_text(encoding="utf-8")
        assert "repository other than `" not in text, golden
        assert "repository other than this project's own (" in text, golden


def test_coordination_guide_does_not_use_output_dir_as_repository() -> None:
    text = (ROOT / "docs_src/cross-repository-coordination-guide.md").read_text(encoding="utf-8")
    assert "PRIMARY_OUTPUT_DIR" not in text.split("## Governance: Constitutional Rule #11", 1)[1].split("\n## ", 1)[0]
