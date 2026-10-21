"""Structural checks for the docs deploy workflow under the 1.0 release-checkout model (B6)."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / ".github/workflows/docs.yml"
AUTO_UPDATE = REPO_ROOT / ".github/workflows/framework-auto-update.yml"


def _on_block(text: str) -> str:
    m = re.search(r"^on:\n((?:[ \t].*\n|\n)+?)(?=^\S)", text, re.MULTILINE)
    assert m, "missing on: block"
    return m.group(1)


def test_docs_publishes_from_final_release_tags():
    on = _on_block(DOCS.read_text(encoding="utf-8"))
    assert re.search(r"^\s+tags:", on, re.MULTILINE), "docs must trigger on tag pushes"
    assert '- "v1.*"' in on
    assert '- "!v1.*-rc.*"' in on, "release candidates must not publish"


def test_docs_no_longer_publishes_every_push_to_main():
    on = _on_block(DOCS.read_text(encoding="utf-8"))
    assert not re.search(r"^\s+branches:", on, re.MULTILINE), "docs must not deploy on branch pushes"


def test_docs_keeps_manual_republish():
    on = _on_block(DOCS.read_text(encoding="utf-8"))
    assert re.search(r"^\s+workflow_dispatch:", on, re.MULTILINE)


def test_docs_checkout_uses_triggering_ref():
    """No pinned ref: actions/checkout must check out the tag that triggered the run."""
    text = DOCS.read_text(encoding="utf-8")
    assert "actions/checkout" in text
    assert not re.search(r"^\s+ref:", text, re.MULTILINE)


def test_framework_auto_update_stays_on_main():
    """Recorded on purpose: its proposals are development changes; backports are decided per PR."""
    text = AUTO_UPDATE.read_text(encoding="utf-8")
    assert re.search(r"^\s+ref:\s*main\s*$", text, re.MULTILINE)
    assert re.search(r"^\s+- cron:", text, re.MULTILINE)
