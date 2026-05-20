"""Plan 2 — backup manifest sidecar tests."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

import build_team
from agentteams import emit

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "backup-manifest.schema.json"
EXAMPLES_DIR = REPO_ROOT / "examples"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _seed_gates(output_dir: Path, monkeypatch):
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", key)
    w = {
        "timestamp": "2026-05-03T00:00:00Z", "waiver_id": "wf-bm",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2099-01-01T00:00:00Z", "max_uses": "9", "uses": "0",
        "approver": "t", "ticket_id": "BM", "reason_code": "T",
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


def _latest_backup(output_dir: Path) -> Path:
    backups = sorted((output_dir / ".agentteams-backups").iterdir())
    assert backups, "no backup directory created"
    return backups[-1]


# ---------------- 1. manifest written for --update ----------------

def test_update_writes_backup_manifest_with_pre_update_reason(tmp_path, monkeypatch):
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
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ]) == 0

    backup = _latest_backup(output_dir)
    manifest_path = backup / emit.BACKUP_MANIFEST_NAME
    assert manifest_path.exists(), "_manifest.json sidecar not written"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    import jsonschema
    jsonschema.Draft7Validator(_schema()).validate(manifest)
    assert manifest["artifact_type"] == "backup-manifest"
    assert manifest["reason"] == "pre-update"
    assert manifest["framework"] == "copilot-vscode"
    assert manifest["total_files"] >= 1
    assert len(manifest["files"]) == manifest["total_files"]


# ---------------- 2. manifest written for --overwrite ----------------

def test_overwrite_writes_backup_manifest_with_overwrite_reason(tmp_path, monkeypatch):
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
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--overwrite", "--yes", "--no-scan", "--security-offline",
    ]) == 0

    backup = _latest_backup(output_dir)
    manifest = json.loads((backup / emit.BACKUP_MANIFEST_NAME).read_text())
    # The --update overwrite path uses 'overwrite-mode' (see build_team.py).
    assert manifest["reason"] == "overwrite-mode"


# ---------------- 3. SHA-256 integrity verifies against on-disk backup ----------------

def test_backup_manifest_sha256_matches_on_disk_files(tmp_path, monkeypatch):
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
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ]) == 0

    backup = _latest_backup(output_dir)
    manifest = json.loads((backup / emit.BACKUP_MANIFEST_NAME).read_text())
    assert manifest["files"], "manifest has no file entries"
    for entry in manifest["files"]:
        bpath = backup / entry["backup_path"]
        assert bpath.exists(), f"backup file missing: {bpath}"
        on_disk = bpath.read_bytes()
        assert len(on_disk) == entry["source_size_bytes"], (
            f"size mismatch for {entry['source_path']}"
        )
        assert hashlib.sha256(on_disk).hexdigest() == entry["source_sha256"], (
            f"SHA-256 mismatch for {entry['source_path']}"
        )
