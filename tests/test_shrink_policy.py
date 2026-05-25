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
