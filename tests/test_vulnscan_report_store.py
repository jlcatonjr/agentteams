"""Tests for agentteams.vulnscan.report_store (WS-1: the agent report store)."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from agentteams.vulnscan import report_store as rs

REQ = "0123456789abcdef0123456789abcdef"


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "alice-state" / "runs" / "2026-10-22-abc123"
    d.mkdir(parents=True)
    return d


def _report(run_dir: Path) -> Path:
    return run_dir / rs.REPORT_DIR / rs.REPORT_FILE


# --- fence -----------------------------------------------------------------------------------

def test_fence_minimum_three() -> None:
    assert rs.fence("hello") == "```text\nhello\n```\n"
    assert rs.fence("") == "```text\n```\n"
    assert rs.fence("a `b` c\n") == "```text\na `b` c\n```\n"


def test_fence_nesting_longer_than_any_run() -> None:
    body = "before\n````\n[link](http://example.invalid) @alice <b>x</b>\n````\nafter"
    out = rs.fence(body)
    bar = out.split("text\n", 1)[0]
    assert set(bar) == {"`"} and len(bar) >= 5
    assert out.endswith(bar + "\n")
    # nothing inside reaches the fence length, so nothing can close it early
    inner = out[len(bar) + len("text\n"):-(len(bar) + 1)]
    assert inner == body + "\n"
    assert max(len(m) for m in re.findall(r"`+", inner)) < len(bar)


def test_fence_long_inline_run() -> None:
    out = rs.fence("x" + "`" * 9 + "y")
    assert out.startswith("`" * 10 + "text\n")


# --- save / load ------------------------------------------------------------------------------

def test_save_load_round_trip(run_dir: Path) -> None:
    md = "# Findings\n\n- fixed CVE-2026-0001 in [repo](http://example.invalid)\n```py\nx = 1\n```\n"
    path = rs.save(run_dir, md, request_id=REQ)
    assert path == _report(run_dir)
    text = rs.load(run_dir)
    assert text is not None
    assert text.endswith(rs.fence(md))
    assert f"- request id: {REQ}" in text
    assert "- run state: pending" in text


def test_modes(run_dir: Path) -> None:
    rs.save(run_dir, "ok", request_id=REQ)
    assert stat.S_IMODE((run_dir / rs.REPORT_DIR).stat().st_mode) == 0o700
    assert stat.S_IMODE(_report(run_dir).stat().st_mode) == 0o600
    # no temp files left behind
    assert sorted(p.name for p in (run_dir / rs.REPORT_DIR).iterdir()) == [rs.REPORT_FILE]


def test_later_save_replaces(run_dir: Path) -> None:
    rs.save(run_dir, "first", request_id=REQ)
    rs.save(run_dir, "second", request_id="op-0011223344556677")
    text = rs.load(run_dir)
    assert text is not None and "second" in text and "first" not in text
    assert "op-0011223344556677" in text


def test_oversize_refused(run_dir: Path) -> None:
    with pytest.raises(ValueError, match="limit"):
        rs.save(run_dir, "x" * (rs.REPORT_MAX_BYTES + 1), request_id=REQ)
    # multibyte characters count as bytes, not characters
    with pytest.raises(ValueError, match="limit"):
        rs.save(run_dir, "é" * (rs.REPORT_MAX_BYTES // 2 + 1), request_id=REQ)
    rs.save(run_dir, "x" * rs.REPORT_MAX_BYTES, request_id=REQ)
    assert rs.load(run_dir) is not None


def test_nul_refused(run_dir: Path) -> None:
    with pytest.raises(ValueError, match="NUL"):
        rs.save(run_dir, "a\x00b", request_id=REQ)
    assert not _report(run_dir).exists()


def test_bad_request_id_refused(run_dir: Path) -> None:
    for bad in ("", "a\nb", "x" * 65, "../etc"):
        with pytest.raises(ValueError, match="request id"):
            rs.save(run_dir, "ok", request_id=bad)


def test_symlink_file_refused(run_dir: Path, tmp_path: Path) -> None:
    rs.save(run_dir, "ok", request_id=REQ)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    _report(run_dir).unlink()
    _report(run_dir).symlink_to(victim)
    with pytest.raises(ValueError):
        rs.load(run_dir)
    # save replaces the link itself (rename over it) and never writes through it
    rs.save(run_dir, "new", request_id=REQ)
    assert victim.read_text() == "keep"
    assert not _report(run_dir).is_symlink()


def test_symlink_dir_refused(run_dir: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (run_dir / rs.REPORT_DIR).symlink_to(elsewhere)
    with pytest.raises(ValueError, match="symlink"):
        rs.save(run_dir, "ok", request_id=REQ)
    assert list(elsewhere.iterdir()) == []
    with pytest.raises(ValueError, match="symlink"):
        rs.load(run_dir)


def test_loose_dir_mode_refused(run_dir: Path) -> None:
    rs.save(run_dir, "ok", request_id=REQ)
    os.chmod(run_dir / rs.REPORT_DIR, 0o755)
    with pytest.raises(ValueError, match="mode 700"):
        rs.load(run_dir)


def test_tampered_body_refused(run_dir: Path) -> None:
    rs.save(run_dir, "honest", request_id=REQ)
    p = _report(run_dir)
    p.write_text(p.read_text().replace("honest", "forged"))
    with pytest.raises(ValueError, match="altered"):
        rs.load(run_dir)


def test_malformed_header_refused(run_dir: Path) -> None:
    rs.save(run_dir, "ok", request_id=REQ)
    p = _report(run_dir)
    p.write_text(p.read_text().replace("- run state: pending", "- run state: [x](http://e.invalid)"))
    with pytest.raises(ValueError, match="malformed"):
        rs.load(run_dir)


def test_load_missing(tmp_path: Path, run_dir: Path) -> None:
    assert rs.load(run_dir) is None
    assert rs.load(tmp_path / "no-such-run") is None


# --- finalize ---------------------------------------------------------------------------------

def test_finalize_writes_run_state(run_dir: Path) -> None:
    md = "report body ```` with fences"
    rs.save(run_dir, md, request_id=REQ)
    rs.finalize(run_dir, "timed-out")
    text = rs.load(run_dir)
    assert text is not None
    assert "- run state: timed-out" in text
    assert re.search(r"- finalized: \d{4}-\d{2}-\d{2}T", text)
    assert text.endswith(rs.fence(md))
    assert stat.S_IMODE(_report(run_dir).stat().st_mode) == 0o600


def test_finalize_unknown_state_refused(run_dir: Path) -> None:
    rs.save(run_dir, "ok", request_id=REQ)
    with pytest.raises(ValueError, match="run state"):
        rs.finalize(run_dir, "pwned\n- run state: completed")


def test_finalize_without_report_is_noop(run_dir: Path) -> None:
    rs.finalize(run_dir, "completed")
    assert not (run_dir / rs.REPORT_DIR).exists()
    assert rs.load(run_dir) is None


# --- render_for_review ------------------------------------------------------------------------

def test_render_no_report(run_dir: Path) -> None:
    out = rs.render_for_review(run_dir)
    assert out.startswith(rs.REVIEW_HEADING + "\n\n")
    assert rs.NO_REPORT_LINE in out


def test_render_with_report_is_inert(run_dir: Path) -> None:
    md = "![img](http://e.invalid/x.png) <script>x</script> @alice\n```\nbreakout?\n```"
    rs.save(run_dir, md, request_id=REQ)
    rs.finalize(run_dir, "completed")
    out = rs.render_for_review(run_dir)
    assert out.startswith(rs.REVIEW_HEADING + "\n\n")
    assert "- run state: completed" in out
    body = out.split("\n\n", 3)[-1]
    assert body.startswith("````text\n") and body.endswith("\n````\n")
