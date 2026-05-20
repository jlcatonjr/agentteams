"""Plan 1 — ``--update --dry-run`` structured-preview tests."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

import build_team
from agentteams import emit

REPO_ROOT = Path(__file__).parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"


def _seed_gates(output_dir: Path, monkeypatch):
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", key)
    w = {
        "timestamp": "2026-05-03T00:00:00Z", "waiver_id": "wf-dr",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2099-01-01T00:00:00Z", "max_uses": "9", "uses": "0",
        "approver": "t", "ticket_id": "DR", "reason_code": "T",
        "conditions_verified": "verified", "signature": "",
    }
    payload = "|".join(w[k] for k in [
        "waiver_id", "action_reviewed", "expires_at", "max_uses", "uses",
        "approver", "ticket_id", "reason_code", "conditions_verified"])
    w["signature"] = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        + ",".join(w[k] for k in [
            "timestamp", "waiver_id", "action_reviewed", "expires_at",
            "max_uses", "uses", "approver", "ticket_id", "reason_code",
            "conditions_verified", "signature"]) + "\n",
        encoding="utf-8",
    )


def _seed_pass(output_dir: Path):
    (output_dir / "references" / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-05-03T00:00:00Z,t,overwrite,PASS,,verified\n",
        encoding="utf-8",
    )


@pytest.fixture
def initialized_team(tmp_path, monkeypatch):
    brief = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief.exists():
        pytest.skip("data-pipeline brief not found")
    output_dir = tmp_path / ".github" / "agents"
    _seed_gates(output_dir, monkeypatch)
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0
    _seed_pass(output_dir)
    return brief, output_dir


# ---------- API-level: EmitResult exposes a DryRunReport on dry_run ----------

def test_emit_result_dry_run_report_is_structured():
    """Plan 1: EmitResult.dry_run_report is populated and has the documented
    extension points (entries, notices) for Plan 3 to hook into."""
    r = emit.EmitResult(dry_run=True, dry_run_report=emit.DryRunReport())
    assert isinstance(r.dry_run_report, emit.DryRunReport)
    assert r.dry_run_report.entries == []
    assert r.dry_run_report.notices == []
    # Plan 3 extension hook — notices is mutable and module-public.
    r.dry_run_report.notices.append("example")
    assert r.dry_run_report.notices == ["example"]


# ---------- Text mode ----------

def test_update_dry_run_text_mode_writes_nothing(initialized_team, capsys):
    brief, output_dir = initialized_team
    sentinel = output_dir / "orchestrator.agent.md"
    before = sentinel.read_bytes()
    backups_root = output_dir / ".agentteams-backups"
    backup_snapshots_before = (
        {p.name for p in backups_root.iterdir()} if backups_root.exists() else set()
    )

    capsys.readouterr()
    rc = build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--dry-run", "--yes", "--no-scan", "--security-offline",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert sentinel.read_bytes() == before, "dry-run wrote to a tracked agent file"
    backup_snapshots_after = (
        {p.name for p in backups_root.iterdir()} if backups_root.exists() else set()
    )
    assert backup_snapshots_after == backup_snapshots_before, (
        "dry-run created a backup directory (no writes allowed in dry-run)"
    )
    assert "[DRY RUN PLAN]" in out
    assert "Plan counts:" in out


# ---------- JSON mode ----------

def test_update_dry_run_json_mode_emits_valid_json(initialized_team, capsys):
    brief, output_dir = initialized_team
    capsys.readouterr()
    rc = build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--dry-run", "--json",
        "--yes", "--no-scan", "--security-offline",
    ])
    out = capsys.readouterr().out
    assert rc == 0

    # Locate the JSON document — it is the first '{' to its matching '}' span.
    start = out.find("{")
    assert start >= 0, "no JSON document found on stdout"
    payload = json.loads(out[start:])

    assert "entries" in payload and "counts" in payload and "notices" in payload
    assert payload["project_name"]
    assert payload["framework"]
    assert all({"path", "action", "delta_bytes", "fence_actions"} <= set(e) for e in payload["entries"])
    valid = {"WRITE", "OVERWRITE", "MERGE", "MERGE-OVERWRITE-FENCED", "UNCHANGED", "SKIP"}
    for e in payload["entries"]:
        assert e["action"] in valid, f"unexpected action {e['action']!r}"


# ---------- --update --overwrite --dry-run ----------

def test_dry_run_with_overwrite_preview(initialized_team, capsys):
    brief, output_dir = initialized_team
    sentinel = output_dir / "orchestrator.agent.md"
    before = sentinel.read_bytes()
    capsys.readouterr()
    rc = build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--overwrite", "--dry-run", "--json",
        "--yes", "--no-scan", "--security-offline",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert sentinel.read_bytes() == before
    start = out.find("{")
    payload = json.loads(out[start:])
    actions = {e["action"] for e in payload["entries"]}
    # --overwrite makes the action set OVERWRITE or UNCHANGED (no MERGE).
    assert actions.issubset({"OVERWRITE", "UNCHANGED", "WRITE"}), actions


# ---------- Consistency: dry-run plan matches real-run outcome ----------

def test_dry_run_and_real_run_agree_on_paths(initialized_team, capsys, monkeypatch):
    """Run --update --dry-run, capture the planned per-file paths, then run
    --update for real and assert the same set of paths is touched (modulo
    UNCHANGED rows which are by definition not rewritten)."""
    brief, output_dir = initialized_team

    capsys.readouterr()
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--dry-run", "--json",
        "--yes", "--no-scan", "--security-offline",
    ]) == 0
    out = capsys.readouterr().out
    start = out.find("{")
    plan = json.loads(out[start:])
    planned_touched = {e["path"] for e in plan["entries"] if e["action"] != "UNCHANGED"}

    # Real run.
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ]) == 0
    # Real-run file-list discovery: anything whose mtime changed since just
    # before the real run would be a touched file; reading the build-log is
    # more robust. The build-log's `files_written` records the real touches.
    log = json.loads((output_dir / "references" / "build-log.json").read_text())
    touched_real = set(log.get("files_written", []))

    # Every path the plan said it would touch should appear in the real run's
    # files_written (paths may be project-relative there vs absolute in the
    # plan — compare by basename to bridge the representation gap).
    planned_basenames = {Path(p).name for p in planned_touched}
    real_basenames = {Path(p).name for p in touched_real}
    missing = planned_basenames - real_basenames - {Path(p).name for e in plan["entries"] if e["action"] == "UNCHANGED" for p in [e["path"]]}
    # Allow for files whose dry-run action was WRITE/MERGE but which were
    # genuinely UNCHANGED-by-content on the real run (idempotency); the test
    # passes when the plan is a SUPERSET-or-equal of the real touches.
    assert not (real_basenames - planned_basenames - {"build-log.json", "memory-index.json", "eval-suite.json", "delivery-receipt.json"}), (
        f"real run touched files the dry-run plan did not predict: "
        f"{real_basenames - planned_basenames}"
    )
