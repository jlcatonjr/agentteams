"""Structural-lint tests for emitted agent documents (metaplan plans P3 + P4).

Enforces the canonical document structure from AUTHORING-GUIDE §3:

- §3.3 — the Invariant Core is the FENCED region; every agent persona must
  carry at least one balanced ``AGENTTEAMS`` fence pair (P3: the fence is the
  authoritative structural boundary).
- §3.1 — every agent persona carries a USER-EDITABLE ``## Project-Specific
  Notes`` section, and it sits outside every fence (P4: structural lint).

Reference and instruction files must NOT carry the agent Project-Specific
Notes section.

These tests render fresh via the pipeline, so they catch generator drift
rather than only stale snapshot drift.
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import _run_pipeline, EXAMPLES_DIR  # noqa: E402

_FENCE_BEGIN = "<!-- AGENTTEAMS:BEGIN"
_FENCE_END = "<!-- AGENTTEAMS:END"
_NOTES = "## Project-Specific Notes"


@pytest.fixture(scope="module")
def emitted_dir() -> Path:
    """A freshly rendered copilot-vscode team to lint."""
    td = Path(tempfile.mkdtemp())
    _run_pipeline(EXAMPLES_DIR / "software-project" / "brief.json", td)
    return td


def _agent_files(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*.agent.md"))


def test_corpus_is_nonempty(emitted_dir: Path) -> None:
    assert _agent_files(emitted_dir), "pipeline emitted no agent files to lint"


def test_every_agent_has_a_fenced_invariant_core(emitted_dir: Path) -> None:
    """§3.3 — the Invariant Core region is machine-bounded by fences."""
    for p in _agent_files(emitted_dir):
        text = p.read_text(encoding="utf-8")
        assert _FENCE_BEGIN in text and _FENCE_END in text, (
            f"{p.name}: no fenced Invariant Core region (AUTHORING-GUIDE §3.3)"
        )


def test_fence_markers_are_balanced(emitted_dir: Path) -> None:
    """P3 — the fence is the authoritative boundary, so it must be well-formed."""
    for p in _agent_files(emitted_dir):
        text = p.read_text(encoding="utf-8")
        assert text.count(_FENCE_BEGIN) == text.count(_FENCE_END), (
            f"{p.name}: unbalanced AGENTTEAMS fence markers"
        )


def test_every_agent_has_project_specific_notes(emitted_dir: Path) -> None:
    """§3.1 / R2 — every agent persona has the USER-EDITABLE section."""
    for p in _agent_files(emitted_dir):
        text = p.read_text(encoding="utf-8")
        assert _NOTES in text, (
            f"{p.name}: missing USER-EDITABLE '{_NOTES}' section"
        )


def test_project_notes_is_outside_every_fence(emitted_dir: Path) -> None:
    """§3.3 — the USER-EDITABLE section must follow the last fence END."""
    for p in _agent_files(emitted_dir):
        text = p.read_text(encoding="utf-8")
        notes_pos = text.index(_NOTES)
        last_end = text.rfind(_FENCE_END)
        assert notes_pos > last_end, (
            f"{p.name}: '{_NOTES}' must sit outside every fence to be USER-EDITABLE"
        )


def test_reference_files_have_no_project_notes(emitted_dir: Path) -> None:
    """Reference files are not agent personas — they must not carry the section."""
    for p in emitted_dir.rglob("references/*.md"):
        assert _NOTES not in p.read_text(encoding="utf-8"), (
            f"{p.name}: reference files must not carry the agent Project-Specific Notes section"
        )
