"""test_orphan_advisory_framework_aware.py — the orphan advisory must see every framework.

`_report_orphan_agent_files` filtered on `.agent.md` in *both* directions — the emitted set
and `output_dir.glob("*.agent.md")`. Only copilot-vscode uses that suffix; claude, copilot-cli,
agents_md and goose all emit `*.md`. On those frameworks the glob matched nothing, so the
advisory reported zero orphans **whatever the tree contained**.

Measured on this repo's own `.claude/agents/`: four deployed files are not emitted by the
current brief — `cohesion-repairer.md`, `retrieval-integrator.md` and two retrieval reference
docs — and the advisory built to surface exactly that was silent for all four.

The repo already knew the right pattern. `cli/generate.py`'s `--adopt-orphans` path reads
`adapter.get_file_extension("agent")`; `bridge_sources.py` pairs the same lookup with a
`SETUP-REQUIRED.md` exclusion. Only the advisory hardcoded the suffix.

`agent_ext` is keyword-only and **required**. A default of `.agent.md` would silently
reintroduce the blindness for the next caller, which is how it arrived.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import build_team


def _manifest() -> dict:
    return {"adopted_agents": [], "tool_agents": []}


def _plant(tmp_path: Path, names: list[str]) -> Path:
    out = tmp_path / "agents"
    out.mkdir()
    for n in names:
        (out / n).write_text("# stub\n", encoding="utf-8")
    return out


# --------------------------------------------------------------------------------------
# The defect: a `.md` framework
# --------------------------------------------------------------------------------------


def test_orphans_are_seen_on_a_dot_md_framework(tmp_path: Path) -> None:
    """claude/copilot-cli/goose emit `*.md`; an orphan there must be reported.

    This is the test that fails against the hardcoded `.agent.md` glob: it returned an empty
    list for a directory that plainly contained an orphan.
    """
    out = _plant(tmp_path, ["navigator.md", "cohesion-repairer.md"])
    rendered = [("navigator.md", "x")]

    orphans = build_team._report_orphan_agent_files(
        rendered, out, _manifest(), agent_ext=".md"
    )

    assert orphans == ["cohesion-repairer.md"], (
        "the advisory did not see an orphan on a .md framework — the glob suffix is "
        "hardcoded again"
    )


def test_setup_required_is_never_an_orphan(tmp_path: Path) -> None:
    """`SETUP-REQUIRED.md` is a build artifact, not an agent, and is not emitted as one.

    Under `.agent.md` the suffix excluded it for free. Under `.md` it does not, and without
    the explicit carve-out the advisory would name a file every build produces. Mirrors the
    exclusion `bridge_sources.py` already applies for the same reason.
    """
    out = _plant(tmp_path, ["navigator.md", "SETUP-REQUIRED.md"])
    rendered = [("navigator.md", "x")]

    orphans = build_team._report_orphan_agent_files(
        rendered, out, _manifest(), agent_ext=".md"
    )

    assert orphans == [], f"SETUP-REQUIRED.md reported as an orphan: {orphans}"


def test_adopted_and_tool_docs_are_still_excluded_under_a_generic_suffix(
    tmp_path: Path,
) -> None:
    """The two existing carve-outs built their names with a literal `.agent.md`.

    Parameterising only the glob would leave them comparing `foo.agent.md` against an
    on-disk `foo.md` and never matching, turning both exclusions off for every `.md`
    framework — a false orphan report naming files for deletion.
    """
    out = _plant(tmp_path, ["navigator.md", "adopted-one.md", "ripgrep.md"])
    rendered = [("navigator.md", "x")]
    manifest = {"adopted_agents": ["adopted-one"], "tool_agents": [{"slug": "ripgrep"}]}

    orphans = build_team._report_orphan_agent_files(
        rendered, out, manifest, agent_ext=".md"
    )

    assert orphans == [], f"adopted agent or tool doc reported as an orphan: {orphans}"


# --------------------------------------------------------------------------------------
# Negative control: the framework that worked must keep working
# --------------------------------------------------------------------------------------


def test_dot_agent_md_framework_is_unchanged(tmp_path: Path) -> None:
    """copilot-vscode is the one framework the advisory handled correctly. Hold it fixed.

    A widened glob is more dangerous than a narrow one here: the advisory names files as
    candidates for manual deletion.
    """
    out = _plant(
        tmp_path,
        ["navigator.agent.md", "stale.agent.md", "references.md", "SETUP-REQUIRED.md"],
    )
    rendered = [("navigator.agent.md", "x")]

    orphans = build_team._report_orphan_agent_files(
        rendered, out, _manifest(), agent_ext=".agent.md"
    )

    assert orphans == ["stale.agent.md"], (
        f"copilot-vscode orphan detection changed: {orphans}. `references.md` is not an "
        "agent file on this framework and must not be swept in."
    )


def test_agent_ext_is_required(tmp_path: Path) -> None:
    """No default. A default is what let the suffix go unexamined for every `.md` framework."""
    out = _plant(tmp_path, ["navigator.md"])
    with pytest.raises(TypeError):
        build_team._report_orphan_agent_files([], out, _manifest())  # type: ignore[call-arg]


def test_the_real_deployed_tree_has_the_orphans_that_prompted_this(tmp_path: Path) -> None:
    """Anti-vacuity: pin the four files measured on 2026-08-03 in this repo's own team.

    Not a demand that they be deleted — whether an orphan should go or the brief should
    re-declare the capability is a maintainer decision. This asserts only that the advisory
    can *see* them, which is what it could not do before.
    """
    agents = Path(build_team.__file__).parent / ".claude/agents"
    if not agents.is_dir():
        pytest.skip("this repo's own .claude/agents is not present")

    rendered = [
        (p.name, "x") for p in agents.glob("*.md")
        if p.name not in {"cohesion-repairer.md", "retrieval-integrator.md"}
    ]
    orphans = build_team._report_orphan_agent_files(
        rendered, agents, _manifest(), agent_ext=".md"
    )
    assert set(orphans) == {"cohesion-repairer.md", "retrieval-integrator.md"}, orphans
