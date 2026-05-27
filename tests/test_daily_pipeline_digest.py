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


def test_operational_json_audit_flags_unknown_high_density(tmp_path, monkeypatch):
    """VI.1: a non-allow-listed JSON file under .github/agents/references/
    with >5% path/hash lines is flagged in the digest section.
    """
    mod = _run_with_root(tmp_path, monkeypatch)
    refs = tmp_path / ".github" / "agents" / "references"
    refs.mkdir(parents=True)
    # 8 lines, 2 with path/hash content -> 25% density.
    (refs / "weird-state.json").write_text(
        '{\n'
        '  "a": 1,\n'
        '  "path": "/Users/op/data",\n'
        '  "b": 2,\n'
        '  "hash": "deadbeef0123456789abcdef0123456789abcdef0123",\n'
        '  "c": 3,\n'
        '  "d": 4,\n'
        '}\n',
        encoding="utf-8",
    )
    body, active = mod._operational_json_audit_section()
    assert active
    assert "weird-state.json" in body


def test_operational_json_audit_silent_on_allowed(tmp_path, monkeypatch):
    """VI.1: files inside _OPERATIONAL_JSON_NAMES never trigger the audit."""
    mod = _run_with_root(tmp_path, monkeypatch)
    refs = tmp_path / ".github" / "agents" / "references"
    refs.mkdir(parents=True)
    (refs / "memory-index.json").write_text(
        '{\n  "path": "/Users/op/agentteams"\n}\n', encoding="utf-8"
    )
    body, active = mod._operational_json_audit_section()
    assert not active
    assert body == ""


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
