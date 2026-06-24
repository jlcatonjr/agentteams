"""Propagation test: Workflow 0A reaches *updated* teams via --update --merge.

This is the decisive proof for the parallelization feature's reach. A fresh
build trivially contains Workflow 0A; the hard guarantee is that an EXISTING
team (built before the feature) gains it on `--update --merge` while its own
user-authored, out-of-fence content survives untouched. We exercise the real
fence-merge engine (`emit._merge_fenced_content`) used by the update path.
"""

from __future__ import annotations

from pathlib import Path

from agentteams import emit

REPO_ROOT = Path(__file__).parent.parent
ORCH_TEMPLATE = REPO_ROOT / "agentteams" / "templates" / "universal" / "orchestrator.template.md"

_SENTINEL = "<!-- USER-SENTINEL-PRESERVE-ME (project-authored, out of fence) -->"


def _new_render() -> str:
    """The current template stands in for a freshly rendered orchestrator."""
    return ORCH_TEMPLATE.read_text(encoding="utf-8")


def _stale_existing(new: str) -> str:
    """Simulate a pre-feature on-disk orchestrator: Workflow 0A removed from the
    available_workflows fenced body, plus a user sentinel added outside all fences.
    """
    start = new.index("### Workflow 0A: Parallelization Analysis")
    end = new.index("### Pre-Execution Security Check")
    stale = new[:start] + new[end:]
    # The workflow *definition* (heading) must be gone; passing references to it
    # elsewhere (e.g. Rule 10) are fine — they are not the definition.
    assert "### Workflow 0A: Parallelization Analysis" not in stale
    # Append user content AFTER the last fence (out-of-fence -> must be preserved).
    return stale + "\n" + _SENTINEL + "\n"


def test_update_merge_injects_workflow_0a_into_existing_team():
    new = _new_render()
    stale = _stale_existing(new)

    result = emit._merge_fenced_content(new, stale)
    assert result.parse_errors == [], result.parse_errors
    merged = result.merged_content

    # 1) The feature reached the updated team...
    assert "Workflow 0A: Parallelization Analysis" in merged
    # 2) ...inside the propagating fence (not appended somewhere stray).
    begin = merged.index("<!-- AGENTTEAMS:BEGIN available_workflows")
    end = merged.index("<!-- AGENTTEAMS:END available_workflows")
    assert begin < merged.index("Workflow 0A: Parallelization Analysis") < end
    # 3) The user's out-of-fence content survived the merge untouched.
    assert _SENTINEL in merged


def test_fresh_render_contains_workflow_0a():
    assert "Workflow 0A: Parallelization Analysis" in _new_render()
