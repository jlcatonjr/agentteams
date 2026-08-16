"""test_capability_key_migration.py — the fleet migration for audit finding W1.

Fixing `agentteams/frameworks/claude.py` reaches teams generated *after* the fix. Front matter
is preserved verbatim by `--update --merge`, so every team generated before 2026-08-06 still
declares `allowed-tools:` — a key Claude Code's subagent schema does not define — and therefore
still grants every agent every tool regardless of what its body claims.

A read-only survey on 2026-08-06 found 54 such agent files across two live repositories.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.front_matter_reconcile import (
    migrate_capability_key,
    survey_capability_keys,
)

OLD = "---\nname: Security\ndescription: sentinel\nallowed-tools: Read, Grep, Glob\n---\n\n# Body\n"
NEW = "---\nname: Security\ndescription: sentinel\ntools: Read, Grep, Glob\n---\n\n# Body\n"


def _team(tmp_path: Path, **files: str) -> Path:
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    for name, text in files.items():
        (agents / f"{name}.md").write_text(text, encoding="utf-8")
    return agents


def test_dry_run_is_the_default_and_writes_nothing(tmp_path: Path) -> None:
    """The realistic caller operates on someone else's repository (Rule S-2)."""
    agents = _team(tmp_path, security=OLD)
    result = migrate_capability_key(agents)  # no dry_run argument
    assert len(result) == 1 and result[0].migrated is False
    assert (agents / "security.md").read_text(encoding="utf-8") == OLD, "dry run wrote to disk"


def test_migration_renames_the_key_and_preserves_the_grant(tmp_path: Path) -> None:
    """The template owns the key's NAME; the project owns its VALUE."""
    agents = _team(tmp_path, security=OLD)
    result = migrate_capability_key(agents, dry_run=False)
    assert len(result) == 1 and result[0].migrated is True
    assert (agents / "security.md").read_text(encoding="utf-8") == NEW


def test_a_widened_grant_is_carried_across_unchanged(tmp_path: Path) -> None:
    """A rename must not become a back door for applying the template's capabilities.

    If migration silently narrowed a project's deliberate grant it would be an unrequested
    capability change — the mirror image of the defect being fixed.
    """
    wide = OLD.replace("Read, Grep, Glob", "Read, Grep, Glob, Bash, Write")
    agents = _team(tmp_path, security=wide)
    migrate_capability_key(agents, dry_run=False)
    assert "tools: Read, Grep, Glob, Bash, Write" in (agents / "security.md").read_text(encoding="utf-8")


def test_an_already_migrated_file_is_left_alone(tmp_path: Path) -> None:
    """Idempotence, and the guard against double-migrating a file that carries both keys."""
    agents = _team(tmp_path, security=NEW)
    assert migrate_capability_key(agents, dry_run=False) == []
    assert (agents / "security.md").read_text(encoding="utf-8") == NEW


def test_a_file_carrying_both_keys_is_not_touched(tmp_path: Path) -> None:
    """`tools:` present means the runtime already reads a real limit; do not disturb it."""
    both = "---\nname: S\ntools: Read\nallowed-tools: Read, Bash\n---\n\n# Body\n"
    agents = _team(tmp_path, security=both)
    assert migrate_capability_key(agents, dry_run=False) == []
    assert (agents / "security.md").read_text(encoding="utf-8") == both


def test_a_file_without_front_matter_is_skipped(tmp_path: Path) -> None:
    agents = _team(tmp_path, notes="# Just a document\n")
    assert migrate_capability_key(agents, dry_run=False) == []


def test_survey_counts_both_key_generations(tmp_path: Path) -> None:
    agents = _team(tmp_path, old=OLD, new=NEW, bare="---\nname: X\n---\n\n# Body\n")
    counts = survey_capability_keys(agents)
    assert counts == {"agents": 3, "superseded": 1, "current": 1, "none": 1}


def test_survey_never_writes(tmp_path: Path) -> None:
    """A census across other people's repositories must be provably read-only."""
    agents = _team(tmp_path, security=OLD)
    before = (agents / "security.md").read_text(encoding="utf-8")
    survey_capability_keys(agents)
    assert (agents / "security.md").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("fn", [migrate_capability_key, survey_capability_keys])
def test_a_missing_directory_is_not_an_error(tmp_path: Path, fn) -> None:
    """A fleet sweep hits repos with no Claude team; that is data, not a crash."""
    result = fn(tmp_path / "nope")
    assert result == [] or result == {"agents": 0, "superseded": 0, "current": 0, "none": 0}


def test_this_repository_is_already_migrated() -> None:
    """The canonical team must not regress to the ignored key."""
    agents = Path(__file__).resolve().parents[1] / ".claude" / "agents"
    if not agents.is_dir():
        pytest.skip("no deployed team in this checkout")
    counts = survey_capability_keys(agents)
    assert counts["superseded"] == 0, (
        f"{counts['superseded']} agent file(s) still declare the ignored key and therefore "
        f"inherit every tool"
    )
    assert counts["current"] > 0, "no agent declares a capability key at all"


# --- the sweep must be wired to EVERY emit path -----------------------------

def test_every_emit_call_site_is_followed_by_the_capability_sweep() -> None:
    """A fix wired to one of two paths is the defect it was fixing.

    `agentteams/cli/generate.py` calls `emit.emit_all` twice: once on the fresh-generation path
    and once on the `--update` path. The capability-key sweep was first wired to the fresh path
    only — so `--update --merge`, the command an actual fleet sweep uses, did not run it.

    Caught by rehearsing against an isolated copy of a real downstream repo: 27 of 31 agents
    migrated, 4 did not, and the run reported success. Counting the call sites is crude, and
    that is the point — it fails loudly when a third emit path is added without the sweep.
    """
    source = (Path(__file__).resolve().parents[1] / "agentteams" / "cli" / "generate.py").read_text(
        encoding="utf-8"
    )
    emit_calls = source.count("emit.emit_all(")
    sweeps = source.count("_sweep_capability_key(output_dir, result")
    assert emit_calls > 0, "no emit call sites found — this test has stopped measuring anything"
    assert sweeps == emit_calls, (
        f"{emit_calls} emit.emit_all call site(s) but {sweeps} capability sweep(s). Every path "
        f"that writes a team must migrate the whole agents directory, or a fleet update reports "
        f"success while leaving bridge-written agents on the ignored key."
    )


# --- P3 (2026-08-15): the succession table now carries a second, non-capability
# entry (user-invocable/user-invokable) — prove the mechanism generalizes rather
# than assuming it, and prove it reaches copilot-vscode's .agent.md extension
# (this module's helpers/other tests only ever exercise Claude's bare .md).

INVOKABLE_OLD = (
    "---\nname: Orchestrator\ndescription: sentinel\nuser-invokable: true\n---\n\n# Body\n"
)
INVOKABLE_NEW = (
    "---\nname: Orchestrator\ndescription: sentinel\nuser-invocable: true\n---\n\n# Body\n"
)


def _copilot_vscode_team(tmp_path: Path, **files: str) -> Path:
    agents = tmp_path / ".github" / "agents"
    agents.mkdir(parents=True)
    for name, text in files.items():
        (agents / f"{name}.agent.md").write_text(text, encoding="utf-8")
    return agents


def test_user_invocable_migration_renames_the_key_and_preserves_the_value(
    tmp_path: Path,
) -> None:
    agents = _copilot_vscode_team(tmp_path, orchestrator=INVOKABLE_OLD)
    result = migrate_capability_key(agents, dry_run=False)
    assert len(result) == 1 and result[0].migrated is True
    assert (agents / "orchestrator.agent.md").read_text(encoding="utf-8") == INVOKABLE_NEW


def test_migration_reaches_copilot_vscode_agent_md_extension(tmp_path: Path) -> None:
    """`*.md` globbing must match `<slug>.agent.md`, not just Claude's bare `<slug>.md`."""
    agents = _copilot_vscode_team(tmp_path, orchestrator=INVOKABLE_OLD)
    counts_before = survey_capability_keys(agents)
    assert counts_before["superseded"] == 0 and counts_before["current"] == 0, (
        "survey_capability_keys only knows the tools/allowed-tools pair — user-invocable is "
        "invisible to it by design; this assertion documents that scope, not a defect"
    )
    migrated = {m.rel_path for m in migrate_capability_key(agents, dry_run=False)}
    assert "orchestrator.agent.md" in migrated


def test_user_invocable_and_allowed_tools_migrate_independently_in_one_file(
    tmp_path: Path,
) -> None:
    """Two succession-table entries firing on the same file must not interfere."""
    both_old = (
        "---\nname: Security\ndescription: sentinel\n"
        "allowed-tools: Read, Grep\nuser-invokable: false\n---\n\n# Body\n"
    )
    agents = _team(tmp_path, security=both_old)
    migrate_capability_key(agents, dry_run=False)
    result = (agents / "security.md").read_text(encoding="utf-8")
    assert "tools: Read, Grep" in result and "allowed-tools" not in result
    assert "user-invocable: false" in result and "user-invokable:" not in result


def test_the_sweep_reaches_bridge_written_files(tmp_path: Path) -> None:
    """The concrete case the call-site gap hid.

    Bridge stubs carry extra front-matter keys (`source`, `source_sha256`, `bridge`) and are
    written by `bridge_subagents`, not by the render pipeline — so nothing in a normal
    `--update --merge` render set touches them.
    """
    bridge_stub = (
        "---\n"
        "name: workstream-expert\n"
        "description: Parametric workstream-expert subagent.\n"
        "source_dir: .github/agents\n"
        "collapsed_experts: 4\n"
        "bridge: copilot-vscode-to-claude\n"
        "allowed-tools: Read, Grep, Glob, Task\n"
        "---\n\n# Body\n"
    )
    agents = _team(tmp_path, security=OLD)
    (agents / "workstream-expert.md").write_text(bridge_stub, encoding="utf-8")

    migrated = {m.rel_path for m in migrate_capability_key(agents, dry_run=False)}
    assert "workstream-expert.md" in migrated
    assert survey_capability_keys(agents)["superseded"] == 0
