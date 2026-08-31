"""A relative ``--output`` must not silently overwrite a directory the operator did not mean.

**Materialised, not hypothetical.** A scratch render with a relative ``--output .claude/agents``
resolved against an unexpected working directory and briefly overwrote this repo's real agent
tree with probe content. Recovered from the tool's own pre-write backup; nothing in the CLI had
objected.

The legitimate-update cases come first in this file on purpose. The realistic way to get this
wrong is a guard so eager it refuses ``--output .github/agents`` — the single most common
invocation there is — which would be worse than the defect. Everything the guard cannot
positively classify as foreign must pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.cli.output_target import (
    _looks_agentteams_generated,
    assess_output_target,
)


def _generated_tree(root: Path, marker: str = "references/build-log.json") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / marker
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    return root


# --- must NOT refuse (the cases a bad guard breaks) ------------------------

def test_an_absolute_output_is_never_questioned(tmp_path):
    """Typing a full path is an unambiguous statement of intent."""
    tree = tmp_path / "some-tracked-tree"
    tree.mkdir()
    (tree / "unrelated.md").write_text("x", encoding="utf-8")
    assert assess_output_target(str(tree), tree) is None


def test_updating_an_existing_generated_team_is_allowed(tmp_path):
    """`--output .github/agents` onto a real team: tracked, non-empty, and completely fine."""
    tree = _generated_tree(tmp_path / ".github" / "agents")
    assert assess_output_target(".github/agents", tree) is None


@pytest.mark.parametrize("marker", [
    "references/build-log.json",
    "references/delivery-receipt.json",
    "SETUP-REQUIRED.md",
])
def test_any_single_generated_marker_is_enough(tmp_path, marker):
    """Teams predate individual markers; one is sufficient evidence."""
    tree = _generated_tree(tmp_path / "agents", marker)
    assert assess_output_target("agents", tree) is None


def test_a_fenced_file_alone_identifies_a_generated_tree(tmp_path):
    """A hand-pruned tree can lose its markers and keep its fences."""
    tree = tmp_path / "agents"
    tree.mkdir()
    (tree / "orchestrator.agent.md").write_text(
        "<!-- AGENTTEAMS:BEGIN content v=1 -->\nx\n<!-- AGENTTEAMS:END content -->\n",
        encoding="utf-8",
    )
    assert _looks_agentteams_generated(tree) is True
    assert assess_output_target("agents", tree) is None


def test_a_nonexistent_target_is_allowed(tmp_path):
    """Fresh generation into a new directory is the normal create path."""
    assert assess_output_target("new/agents", tmp_path / "new" / "agents") is None


def test_an_empty_directory_is_allowed(tmp_path):
    tree = tmp_path / "empty"
    tree.mkdir()
    assert assess_output_target("empty", tree) is None


def test_an_untracked_foreign_directory_is_allowed(tmp_path):
    """Scratch dirs outside git are the normal probe case — warning here would be noise."""
    tree = tmp_path / "scratch"
    tree.mkdir()
    (tree / "notes.md").write_text("nothing to do with agentteams", encoding="utf-8")
    assert assess_output_target("scratch", tree) is None


def test_no_output_given_is_allowed(tmp_path):
    assert assess_output_target(None, tmp_path) is None


# --- must refuse (the case that actually happened) -------------------------

def test_a_relative_output_onto_a_foreign_tracked_tree_is_refused(tmp_path, monkeypatch):
    """Tracked, non-empty, and no sign of agentteams — the incident's shape."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "readme.md").write_text("someone else's content", encoding="utf-8")
    import subprocess
    for cmd in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True)

    message = assess_output_target("docs", repo / "docs")

    assert message is not None
    assert "git-tracked" in message
    assert "no sign of being an agentteams-generated tree" in message
    assert "--allow-foreign-output" in message, "must name the escape hatch"
    assert "cwd:" in message, "must show where the relative path resolved from"


def test_the_refusal_explains_why_relative_paths_are_the_hazard(tmp_path):
    repo = tmp_path / "repo"
    (repo / "x").mkdir(parents=True)
    (repo / "x" / "f.md").write_text("y", encoding="utf-8")
    import subprocess
    for cmd in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True)

    message = assess_output_target("x", repo / "x")
    assert message and "resolves against the current working directory" in message


# --- classification helper -------------------------------------------------

def test_an_unreadable_directory_is_not_called_foreign(tmp_path, monkeypatch):
    """Fail-open: inability to classify must never become a refusal."""
    tree = tmp_path / "agents"
    tree.mkdir()

    def _boom(*_a, **_k):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "glob", _boom)
    assert _looks_agentteams_generated(tree) is True
