"""Tests for scripts/daily_pipeline_digest.py."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "daily_pipeline_digest.py"


def _run_with_root(tmp_path: Path, monkeypatch):
    """Execute the digest script with its ROOT/TMP redirected to tmp_path."""
    # The script uses module-level ROOT = Path(__file__).resolve().parents[1].
    # Easiest reroute: monkeypatch by importing the module and overriding
    # constants before calling main().
    import importlib.util

    spec = importlib.util.spec_from_file_location("dpd", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = tmp_path
    mod.TMP = tmp_path / "tmp"
    mod.DAILY = mod.TMP / "daily-pipeline"
    return mod


def test_no_inputs_no_digest(tmp_path, capsys, monkeypatch):
    mod = _run_with_root(tmp_path, monkeypatch)
    rc = mod.main([])
    assert rc == 0
    assert not (tmp_path / "tmp" / "daily-pipeline" / "digest").exists()
    assert "no active signals" in capsys.readouterr().out


def test_with_inputs_writes_digest(tmp_path, capsys, monkeypatch):
    mod = _run_with_root(tmp_path, monkeypatch)

    fr_dir = tmp_path / "tmp" / "daily-pipeline" / "framework-research"
    fr_dir.mkdir(parents=True)
    (fr_dir / "latest.json").write_text(
        json.dumps({
            "generated_at": f"{mod.TODAY}T00:00:00Z",
            "frameworks": {
                "claude": {"fetch_status": "ok", "upstream_tokens": {"front_matter_keys_present": ["name"]}},
            },
        }),
        encoding="utf-8",
    )

    shrink_dir = tmp_path / "tmp" / "daily-pipeline" / "shrink-events"
    shrink_dir.mkdir(parents=True)
    (shrink_dir / f"{mod.TODAY}.md").write_text(
        "# Fenced-Region Shrink Events — test\n\nsome content\n", encoding="utf-8"
    )

    rc = mod.main([])
    assert rc == 0
    digest_path = tmp_path / "tmp" / "daily-pipeline" / "digest" / f"{mod.TODAY}.md"
    assert digest_path.exists()
    body = digest_path.read_text(encoding="utf-8")
    assert "Framework research" in body
    assert "shrink events" in body.lower()
    assert "tmp/daily-pipeline/framework-research/latest.json" in body
