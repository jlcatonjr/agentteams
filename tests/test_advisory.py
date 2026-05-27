"""Tests for agentteams.advisory aggregator (rc.6)."""

from __future__ import annotations

import pytest

from agentteams import advisory


@pytest.fixture
def tmp_daily(tmp_path, monkeypatch):
    """Redirect the DAILY constant to a tmp tree so tests are isolated."""
    monkeypatch.setattr(advisory, "DAILY", tmp_path / "tmp" / "daily-pipeline")
    # ROOT also points away from real working tree (budget_section uses it).
    monkeypatch.setattr(advisory, "ROOT", tmp_path)
    return tmp_path / "tmp" / "daily-pipeline"


def _write_log(daily_root, subdir: str, today: str, content: str):
    d = daily_root / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{today}.md").write_text(content, encoding="utf-8")


def test_aggregate_empty_when_no_findings(tmp_daily):
    """No tmp logs + no .github/agents → empty aggregate."""
    body = advisory.aggregate(today="2026-05-27")
    assert body == ""


def test_aggregate_with_shrink_findings(tmp_daily):
    _write_log(tmp_daily, "shrink-events", "2026-05-27",
               "# Fenced-Region Shrink Events\n\nsome shrink content\n")
    body = advisory.aggregate(today="2026-05-27")
    assert "Daily-Pipeline Advisory" in body
    assert "Fenced-region shrink events" in body
    assert "some shrink content" in body


def test_aggregate_with_orphan_findings(tmp_daily):
    _write_log(tmp_daily, "orphan-events", "2026-05-27",
               "# Orphan Agent Events\n\nstale-a.agent.md listed\n")
    body = advisory.aggregate(today="2026-05-27")
    assert "Orphan agent files" in body
    assert "stale-a.agent.md" in body
    assert "`@cleanup`" in body


def test_aggregate_with_operational_json_section(tmp_daily):
    """A previously-rendered daily digest with an operational-JSON section
    contributes that section to the advisory."""
    digest_dir = tmp_daily / "digest"
    digest_dir.mkdir(parents=True)
    (digest_dir / "2026-05-27.md").write_text(
        "# Daily-Pipeline Quality Digest\n\n"
        "## Other section\n\nfoo\n\n"
        "## Operational-JSON allow-list audit\n\n"
        "| File | Flagged lines | Total lines |\n"
        "|---|---|---|\n"
        "| `weird.json` | 3 | 20 |\n\n",
        encoding="utf-8",
    )
    body = advisory.aggregate(today="2026-05-27")
    assert "Operational-JSON allow-list audit" in body
    assert "weird.json" in body


def test_hash_stable_across_calls(tmp_daily):
    _write_log(tmp_daily, "orphan-events", "2026-05-27",
               "# Orphan Agent Events\n\nfoo.agent.md\n")
    body1 = advisory.aggregate(today="2026-05-27")
    body2 = advisory.aggregate(today="2026-05-27")
    assert advisory.hash_body(body1) == advisory.hash_body(body2)
    assert len(advisory.hash_body(body1)) == 12


def test_hash_changes_with_content(tmp_daily):
    _write_log(tmp_daily, "orphan-events", "2026-05-27",
               "# Orphan Agent Events\n\nfoo.agent.md\n")
    body_a = advisory.aggregate(today="2026-05-27")
    _write_log(tmp_daily, "orphan-events", "2026-05-27",
               "# Orphan Agent Events\n\nfoo.agent.md\nbar.agent.md\n")
    body_b = advisory.aggregate(today="2026-05-27")
    assert advisory.hash_body(body_a) != advisory.hash_body(body_b)
