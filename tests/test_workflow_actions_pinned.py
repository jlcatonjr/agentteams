"""WORKFLOW-ACTIONS-NOT-SHA-PINNED: every third-party action is pinned to a full commit SHA.

A tag or branch ref (``@v7``, ``@main``) is mutable: whoever controls the action's
repository can move it, and the next run executes the new code with this repo's
token. Each ``uses:`` must name a 40-character commit SHA, with the tag it was
resolved from as a trailing comment (``uses: actions/checkout@<sha> # v7``).
Local actions (``./``) and ``docker://`` images are exempt.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>['\"]?[^\s'\"#]+['\"]?)(?P<rest>.*)$")
_PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
_TAG_COMMENT = re.compile(r"^\s+#\s*\S+")


def _workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def unpinned_uses(text: str) -> list[str]:
    """Return each ``uses:`` line in ``text`` that is not pinned to a full commit SHA.

    Args:
        text: The workflow file's contents.

    Returns:
        The offending lines, stripped; empty when every action is pinned.
    """
    bad = []
    for line in text.splitlines():
        m = _USES.match(line)
        if not m:
            continue
        ref = m.group("ref").strip("'\"")
        if ref.startswith("./") or ref.startswith("docker://"):
            continue
        if not _PINNED.match(ref) or not _TAG_COMMENT.match(m.group("rest")):
            bad.append(line.strip())
    return bad


def test_workflows_exist():
    assert _workflow_files(), f"no workflow files found under {WORKFLOWS}"


def test_every_third_party_action_is_sha_pinned():
    offenders = {
        p.name: bad for p in _workflow_files() if (bad := unpinned_uses(p.read_text(encoding="utf-8")))
    }
    assert not offenders, f"tag/branch-pinned actions (pin to a commit SHA + '# <tag>'): {offenders}"


def test_detector_rejects_tags_branches_and_short_shas():
    for line in (
        "      - uses: actions/checkout@v7",
        "        uses: actions/setup-python@main",
        "        uses: actions/checkout@3d3c42e",
        "        uses: 'actions/checkout@v7'",
        "        uses: actions/checkout@" + "a" * 40,  # no tag comment
        "        uses: actions/checkout@" + "A" * 40 + " # v7",  # not lowercase hex
    ):
        assert unpinned_uses(line), line


def test_detector_accepts_pins_local_and_docker():
    for line in (
        "      - uses: actions/checkout@" + "0" * 40 + " # v7",
        "        uses: github/codeql-action/init@" + "f" * 40 + " # v3",
        "      - uses: ./.github/actions/local",
        "        uses: docker://alpine:3.20",
    ):
        assert not unpinned_uses(line), line
