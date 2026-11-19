"""Generated teams must not be told to run commands that exist only in the agentteams checkout (IM item 23).

A consumer team has the installed ``agentteams`` entry point. It has no ``build_team.py`` (that file sits at the
root of this repository) and no ``src/`` package. Gaps: G-06-2 (navigator), G-19-4 (agent-updater and the other
universal templates, plus the builder templates every team gets), G-12-3 (agent-updater's false claim about bare
``--update``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "agentteams" / "templates"

#: agentteams' own authoring docs and audit sheet (paths relative to ``agentteams/templates/``). Never rendered into a
#: team, so they may name the checkout's script.
NOT_RENDERED = {"AUTHORING-GUIDE.md", "template-chapter-audit.csv"}

#: An instruction to run a repo-only command: ``python [path/]build_team.py``, ``build_team.py --flag`` or a ``\``
#: continuation, and ``python -m src.<module>`` / ``python -m build_team``. A bare mention of the file (a staged
#: path, a gate comment) is fine.
REPO_ONLY = re.compile(
    r"\bpython3?(?:\s+-\w+)*\s+(?:\S*/)?build_team\.py"
    r"|\bbuild_team\.py\s+(?:--|\\)"
    r"|\bpython3?(?:\s+-\w+)*\s+-m\s+(?:src\.|build_team\b)"
)


def _rendered_templates() -> list[Path]:
    """Every text file under the template root (``*.template.md``, ``*.json.template``, ``*.csv.template``, the
    sandbox ``*.sh`` scripts, hooks …) except :data:`NOT_RENDERED`. Binary files (a NUL byte) are skipped."""
    return [
        p for p in sorted(TEMPLATES.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
        and p.relative_to(TEMPLATES).as_posix() not in NOT_RENDERED and b"\0" not in p.read_bytes()
    ]


def test_the_lint_pattern_catches_each_repo_only_form() -> None:
    for line in ("python build_team.py --description b.json --check", "python3 -B build_team.py --self",
                 "`build_team.py --add-fence-markers x`", "python build_team.py \\", "build_team.py \\",
                 "python -m src.graph .github/agents/", "python ./build_team.py --check",
                 "python3 ~/agentteams/build_team.py --update", "python -m build_team --check"):
        assert REPO_ONLY.search(line), line
    for line in ("when `build_team.py`, `agentteams/cli/` or `agentteams/man.py` is staged",
                 "# `agentteams`, `python -m agentteams.cli.app` or `build_team.py`; the Python API too.",
                 "agentteams --description <brief> --check", "python -m agentteams.governance_logs check ."):
        assert not REPO_ONLY.search(line), line


@pytest.mark.parametrize("template", _rendered_templates(), ids=lambda p: p.relative_to(TEMPLATES).as_posix())
def test_no_rendered_template_names_a_repo_only_command(template: Path) -> None:
    hits = [f"{n}: {line.strip()}" for n, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1)
            if REPO_ONLY.search(line)]
    assert not hits, "use the installed `agentteams` entry point, not the checkout's script:\n" + "\n".join(hits)


def _text(rel: str) -> str:
    return (TEMPLATES / rel).read_text(encoding="utf-8")


def test_g06_2_the_navigator_refreshes_with_the_installed_cli() -> None:
    text = _text("universal/navigator.template.md")
    assert "agentteams --refresh-graph" in text
    assert "agentteams --description <brief.json> --update --merge --yes" in text


def test_g19_4_the_agent_updater_workflow_uses_the_installed_cli() -> None:
    text = _text("universal/agent-updater.template.md")
    for flag in ("--check", "--update --merge", "--scan-security"):
        assert f"agentteams --description <brief> {flag}" in text, flag
    assert "build_team.py" not in text


@pytest.mark.parametrize("framework", ["claude", "copilot-cli", "copilot-vscode", "goose", "grok"])
def test_g19_4_every_builder_invokes_the_installed_cli(framework: str) -> None:
    text = _text(f"builder/team-builder-{framework}.template.md")
    assert "build_team.py" not in text
    assert re.search(r"^agentteams \\$", text, re.M) or "agentteams --description" in text


def test_g12_3_the_agent_updater_does_not_claim_bare_update_overwrites() -> None:
    text = _text("universal/agent-updater.template.md")
    assert "Bare `--update` overwrites" not in text
    assert "not bare `--update`" not in text
    assert "`--update` and `--update --merge` are equivalent in current defaults" in text
    assert "only `--update --overwrite` replaces whole files" in text
