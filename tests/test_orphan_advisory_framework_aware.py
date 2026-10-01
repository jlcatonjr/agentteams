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


# --------------------------------------------------------------------------------------
# Follow-up #15: goose `.yaml` legacy tool agents, and relabelled (not silenced) keep buckets
# --------------------------------------------------------------------------------------


def _goose_tool_manifest() -> dict:
    return {"project_name": "Demo", "adopted_agents": [], "tool_agents": [{"slug": "tool-x"}]}


def test_goose_legacy_tool_yaml_is_caught_by_the_sweep_dry_run(tmp_path: Path, capsys) -> None:
    """The sweep used a hardcoded suffix map with no goose entry; `tool-x.yaml` evaded it."""
    out = _plant(tmp_path, ["tool-x.yaml", "navigator.yaml"])
    removed, notices = build_team._remove_stale_tool_agents(
        _goose_tool_manifest(), out, "goose", overwrite=True, dry_run=True, agent_ext=".yaml"
    )
    assert removed == [str(out / "tool-x.yaml")] and notices == []
    assert "[DRY RUN] REMOVE" in capsys.readouterr().out
    assert (out / "tool-x.yaml").exists()


def test_goose_legacy_tool_yaml_overwrite_backs_up_then_deletes(tmp_path: Path) -> None:
    out = _plant(tmp_path, ["tool-x.yaml", "navigator.yaml"])
    removed, notices = build_team._remove_stale_tool_agents(
        _goose_tool_manifest(), out, "goose", overwrite=True, dry_run=False, agent_ext=".yaml"
    )
    assert removed == [str(out / "tool-x.yaml")] and notices == []
    assert not (out / "tool-x.yaml").exists() and (out / "navigator.yaml").exists()
    backups = list((out / ".agentteams-backups").rglob("tool-x.yaml"))
    assert backups and backups[0].read_text(encoding="utf-8") == "# stub\n"


def test_overwrite_never_deletes_through_a_symlink(tmp_path: Path) -> None:
    """Regular files only: a planted `tool-x.yaml -> elsewhere` is not a candidate."""
    out = _plant(tmp_path, ["navigator.yaml"])
    target = tmp_path / "precious.txt"
    target.write_text("keep", encoding="utf-8")
    (out / "tool-x.yaml").symlink_to(target)
    removed, _ = build_team._remove_stale_tool_agents(
        _goose_tool_manifest(), out, "goose", overwrite=True, dry_run=False, agent_ext=".yaml"
    )
    assert removed == [] and target.read_text(encoding="utf-8") == "keep"
    assert (out / "tool-x.yaml").is_symlink()


def test_overwrite_refuses_when_the_backup_cannot_be_verified(tmp_path: Path, monkeypatch) -> None:
    from agentteams import emit
    from agentteams.backup import BackupResult

    out = _plant(tmp_path, ["tool-x.yaml"])
    monkeypatch.setattr(emit, "backup_output_dir", lambda *a, **k: BackupResult())
    removed, notices = build_team._remove_stale_tool_agents(
        _goose_tool_manifest(), out, "goose", overwrite=True, dry_run=False, agent_ext=".yaml"
    )
    assert removed == [] and (out / "tool-x.yaml").exists()
    assert notices and "NOT removed" in notices[0]


def test_sweep_agent_ext_is_required(tmp_path: Path) -> None:
    out = _plant(tmp_path, ["tool-x.yaml"])
    with pytest.raises(TypeError):
        build_team._stale_tool_agent_paths(_goose_tool_manifest(), out, "goose")  # type: ignore[call-arg]


def test_goose_buckets_bespoke_and_bridge_are_kept_not_orphaned(tmp_path: Path, capsys, monkeypatch) -> None:
    persisted: list[list[str]] = []
    monkeypatch.setattr(build_team, "_persist_orphan_events", lambda o, m, d: persisted.append(o))
    out = _plant(
        tmp_path,
        ["navigator.yaml", "interpretation-advisor.yaml", "bridge-orchestrator.yaml", "stale.yaml"],
    )
    (out / "forged.yaml").write_text('bridge: true\nversion: "1.0.0"\n', encoding="utf-8")
    manifest = {
        "adopted_agents": [], "tool_agents": [],
        "agent_slug_list": ["orchestrator", "navigator", "interpretation-advisor"],
    }

    orphans = build_team._report_orphan_agent_files(
        [("navigator.yaml", "x")], out, manifest, agent_ext=".yaml"
    )
    err = capsys.readouterr().err

    assert orphans == ["forged.yaml", "stale.yaml"]
    assert persisted == [["forged.yaml", "stale.yaml"]], "only the orphaned bucket is persisted"
    assert "interpretation-advisor.yaml  — bespoke roster member (no template): keep" in err
    assert "bridge-orchestrator.yaml  — bridge-managed: keep" in err
    # A front-matter claim is self-asserted: labelled, but never moved to a keep bucket.
    assert "forged.yaml  — claims bridge-managed (unverified)" in err
