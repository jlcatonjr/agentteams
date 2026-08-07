"""test_redteam_instantiate.py — the guards on fresh multi-framework generation.

Written late, and that is worth recording. Plan A's step 9 was marked **done** in its steps CSV
while this file did not exist, so `instantiate.py` shipped with two safety guards that had never
been proven to fire. That is F-1 applied to my own work — a control nobody has watched fail —
and it is the second time in this conversation a step was marked done without being done.

The two guards, and why each exists:

* **Isolation.** Generation must refuse a destination inside the source tree. Building the
  agent trees *in place*, immediately before attacking them, mutates the thing under test on
  the operator's machine and would trip the deterministic audit's own live-tree check.
* **Zero files is an error.** A framework that emits nothing sweeps clean, and "0 failures over
  0 agents" renders as success — F-4 with the denominator at zero.

Both are cheap to state and easy to get wrong in the direction that looks fine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.redteam import instantiate

REPO_ROOT = Path(__file__).resolve().parents[1]


# ===========================================================================
# isolation
# ===========================================================================

def test_generating_into_the_source_tree_is_refused(tmp_path: Path) -> None:
    """Fires: a destination inside the source is not an isolated build."""
    source = tmp_path / "repo"
    (source / ".github" / "agents").mkdir(parents=True)
    brief = source / "brief.json"
    brief.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="inside"):
        instantiate.instantiate_all(
            source_root=source, brief=brief, dest_root=source / "trees", frameworks=("goose",)
        )


def test_generating_into_the_source_root_itself_is_refused(tmp_path: Path) -> None:
    """The degenerate case: dest == source."""
    source = tmp_path / "repo"
    source.mkdir()
    brief = source / "brief.json"
    brief.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="inside"):
        instantiate.instantiate_all(
            source_root=source, brief=brief, dest_root=source, frameworks=("goose",)
        )


def test_a_sibling_destination_is_allowed(tmp_path: Path) -> None:
    """Negative control: a destination outside the source passes the isolation check.

    Without this, a guard that rejected *every* destination would satisfy both tests above and
    make the module unusable — the refusal has to be specific to the unsafe case.
    """
    source = tmp_path / "repo"
    source.mkdir()
    brief = source / "brief.json"
    brief.write_text("{}", encoding="utf-8")

    # Fails later (the brief is empty and generation will not succeed), but NOT on isolation.
    try:
        instantiate.instantiate_all(
            source_root=source, brief=brief, dest_root=tmp_path / "elsewhere",
            frameworks=(), timeout=5,
        )
    except ValueError as exc:  # pragma: no cover - only reached on a regression
        assert "inside" not in str(exc), "a sibling destination was wrongly refused"


def test_a_missing_brief_is_refused(tmp_path: Path) -> None:
    """The canonical source must exist. Generating from nothing produces empty trees."""
    source = tmp_path / "repo"
    source.mkdir()
    with pytest.raises(FileNotFoundError):
        instantiate.instantiate_all(
            source_root=source, brief=source / "absent.json",
            dest_root=tmp_path / "out", frameworks=("goose",),
        )


# ===========================================================================
# zero files is an error, not a clean sweep
# ===========================================================================

def _tree(framework: str, count: int, tmp_path: Path) -> instantiate.FrameworkTree:
    files = []
    for index in range(count):
        path = tmp_path / f"{framework}-{index}.md"
        path.write_text("agent", encoding="utf-8")
        files.append(path)
    return instantiate.FrameworkTree(
        framework=framework, root=tmp_path, agents_dir=tmp_path, agent_files=files
    )


def test_a_framework_that_emitted_nothing_is_a_problem(tmp_path: Path) -> None:
    """Fires: an empty tree must not read as a framework with no failures."""
    result = instantiate.Instantiation(
        dest_root=tmp_path, brief=tmp_path / "b.json", brief_digest="d", git_head="h",
        trees={"goose": _tree("goose", 0, tmp_path)},
    )

    problems = result.problems()

    assert len(problems) == 1
    assert "ZERO agent files" in problems[0]
    assert result.ok is False


def test_a_framework_that_errored_is_a_problem(tmp_path: Path) -> None:
    tree = _tree("goose", 0, tmp_path)
    tree.error = "generation exited 2"
    result = instantiate.Instantiation(
        dest_root=tmp_path, brief=tmp_path / "b.json", brief_digest="d", git_head="h",
        trees={"goose": tree},
    )
    assert any("exited 2" in p for p in result.problems())
    assert result.ok is False


def test_populated_trees_report_no_problems(tmp_path: Path) -> None:
    """Negative control: a guard that flagged healthy trees would be muted immediately."""
    result = instantiate.Instantiation(
        dest_root=tmp_path, brief=tmp_path / "b.json", brief_digest="d", git_head="h",
        trees={"goose": _tree("goose", 3, tmp_path), "claude": _tree("claude", 3, tmp_path)},
    )
    assert result.problems() == []
    assert result.ok is True


def test_no_trees_at_all_is_not_ok(tmp_path: Path) -> None:
    """Anti-vacuity: an instantiation that generated nothing must not report ok."""
    result = instantiate.Instantiation(
        dest_root=tmp_path, brief=tmp_path / "b.json", brief_digest="d", git_head="h",
    )
    assert result.ok is False


# ===========================================================================
# enumeration comes from the registry, not from a table here
# ===========================================================================

def test_agent_enumeration_excludes_non_agent_documents(tmp_path: Path) -> None:
    """`SETUP-REQUIRED.md` was swept as an agent, costing real money.

    Real money: a bounded sweep asked a setup document to defend against prompt injection
    before this was fixed. The exclusion list is reused from `bridge_sources`, which already
    had to answer the same question.
    """
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    for name in ("security.md", "orchestrator.md", "SETUP-REQUIRED.md", "README.md"):
        (agents_dir / name).write_text("x", encoding="utf-8")
    (agents_dir / "references").mkdir()
    (agents_dir / "references" / "some-reference.md").write_text("x", encoding="utf-8")

    found = {p.name for p in instantiate.agent_files_for("claude", tmp_path)}

    assert found == {"security.md", "orchestrator.md"}, (
        f"enumeration returned non-agent documents: {found}"
    )


def test_enumeration_is_not_recursive(tmp_path: Path) -> None:
    """A recursive walk counted 55 'agents' for claude against 29 for the others.

    29 agents plus 26 reference documents. Every claude denominator would have been inflated,
    and the sweep would have looked like it covered twice what it did.
    """
    agents_dir = tmp_path / ".claude" / "agents"
    (agents_dir / "references").mkdir(parents=True)
    (agents_dir / "a.md").write_text("x", encoding="utf-8")
    for index in range(5):
        (agents_dir / "references" / f"r{index}.md").write_text("x", encoding="utf-8")

    assert len(instantiate.agent_files_for("claude", tmp_path)) == 1
