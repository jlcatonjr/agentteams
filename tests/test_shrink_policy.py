"""Tests for T2.D5 shrink_policy behaviour in agentteams.emit.emit_all."""

from __future__ import annotations

from pathlib import Path

from agentteams import emit


# A minimal fenced file whose existing body contains backtick identifiers
# that the new render drops — triggers _detect_fence_shrink rule (c).
EXISTING = """# Demo

Outside-fence content.

<!-- AGENTTEAMS:BEGIN demo v=1 -->
- item one referencing `CVE-2024-AAAA`
- item two referencing `CVE-2024-BBBB`
- item three referencing `CVE-2024-CCCC`
- item four referencing `CVE-2024-DDDD`
<!-- AGENTTEAMS:END demo -->

More outside content.
"""

NEW_RENDER = """# Demo

Outside-fence content.

<!-- AGENTTEAMS:BEGIN demo v=1 -->
- item one
<!-- AGENTTEAMS:END demo -->

More outside content.
"""


def _setup(tmp_path: Path) -> Path:
    target = tmp_path / "demo.agent.md"
    target.write_text(EXISTING, encoding="utf-8")
    return target


def test_warn_writes_smaller_content(tmp_path):
    target = _setup(tmp_path)
    result = emit.emit_all(
        [("demo.agent.md", NEW_RENDER)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        shrink_policy="warn",
    )
    assert result.notices, "warn should still surface notices"
    assert not result.shrink_blocked
    body = target.read_text(encoding="utf-8")
    assert "CVE-2024-AAAA" not in body  # shrink occurred
    assert "item one" in body


def test_halt_skips_write_and_records_blocked(tmp_path):
    target = _setup(tmp_path)
    result = emit.emit_all(
        [("demo.agent.md", NEW_RENDER)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        shrink_policy="halt",
    )
    assert result.notices, "halt should still surface notices for visibility"
    assert result.shrink_blocked == [str(target)]
    body = target.read_text(encoding="utf-8")
    # Original CVE refs preserved because the write was refused.
    assert "CVE-2024-AAAA" in body
    assert "CVE-2024-DDDD" in body


def test_dry_run_halt_preflight_lists_blocked_without_writing(tmp_path):
    """T5.1 / IV.1: dry-run + halt populates shrink_blocked with the file
    that a real run would refuse to write, without touching the file.
    """
    target = _setup(tmp_path)
    original_bytes = target.read_bytes()
    result = emit.emit_all(
        [("demo.agent.md", NEW_RENDER)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        dry_run=True,
        shrink_policy="halt",
    )
    assert result.notices
    assert str(target) in result.shrink_blocked
    # File must be byte-identical to before — dry-run never writes.
    assert target.read_bytes() == original_bytes


def test_preserve_keeps_enriched_body_and_is_default(tmp_path):
    """Respectful update: the default 'preserve' policy keeps the richer
    existing fence body instead of overwriting it with the thinner render,
    surfaces a notice, writes no sidecar, and blocks nothing."""
    target = _setup(tmp_path)
    result = emit.emit_all(
        [("demo.agent.md", NEW_RENDER)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        # shrink_policy omitted on purpose — 'preserve' is the default.
    )
    body = target.read_text(encoding="utf-8")
    # Enriched concrete refs survive — nothing lost.
    assert "CVE-2024-AAAA" in body
    assert "CVE-2024-DDDD" in body
    # The thin template body was NOT applied to the shrinking fence.
    assert body.count("item one") == 1 and "item two" in body
    # Visible, non-blocking notice with the preserve wording.
    assert result.notices
    assert any("retained existing enriched body" in n for n in result.notices)
    assert not result.shrink_blocked


def test_preserve_still_updates_non_shrinking_fences(tmp_path):
    """preserve must not freeze the whole file: fences that don't shrink still
    receive their template updates."""
    existing = (
        "# Demo\n\n"
        "<!-- AGENTTEAMS:BEGIN rich v=1 -->\n"
        "- keep `CVE-2024-AAAA`\n- keep `CVE-2024-BBBB`\n"
        "- keep `CVE-2024-CCCC`\n- keep `CVE-2024-DDDD`\n"
        "<!-- AGENTTEAMS:END rich -->\n\n"
        "<!-- AGENTTEAMS:BEGIN plain v=1 -->\n"
        "old plain body\n"
        "<!-- AGENTTEAMS:END plain -->\n"
    )
    new_render = (
        "# Demo\n\n"
        "<!-- AGENTTEAMS:BEGIN rich v=1 -->\n"
        "- only one\n"
        "<!-- AGENTTEAMS:END rich -->\n\n"
        "<!-- AGENTTEAMS:BEGIN plain v=1 -->\n"
        "new improved plain body with more detail and guidance\n"
        "<!-- AGENTTEAMS:END plain -->\n"
    )
    target = tmp_path / "demo.agent.md"
    target.write_text(existing, encoding="utf-8")
    result = emit.emit_all(
        [("demo.agent.md", new_render)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        shrink_policy="preserve",
    )
    body = target.read_text(encoding="utf-8")
    # Shrinking fence preserved...
    assert "CVE-2024-AAAA" in body and "CVE-2024-DDDD" in body
    # ...while the non-shrinking fence was updated to the new template body.
    assert "new improved plain body" in body
    assert "old plain body" not in body
    assert not result.shrink_blocked


def test_allow_writes_silently(tmp_path):
    target = _setup(tmp_path)
    result = emit.emit_all(
        [("demo.agent.md", NEW_RENDER)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        shrink_policy="allow",
    )
    assert not result.notices, "allow should suppress notices"
    assert not result.shrink_blocked
    body = target.read_text(encoding="utf-8")
    assert "CVE-2024-AAAA" not in body  # shrink occurred silently
