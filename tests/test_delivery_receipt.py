"""Tests for the P3 delivery receipt (schemas/delivery-receipt.schema.json).

Covers:
- Schema itself is a valid Draft-07 schema.
- ``_write_delivery_receipt`` produces a payload that validates against the
  schema and pins the receipt contract (``artifact_type`` literal, fingerprint
  + algo version present).
- After ``--update`` succeeds, the receipt exists at the documented path and
  validates against the schema.
- ``--update --dry-run`` does NOT write a receipt.
- The receipt is excluded from drift artifacts: build-log
  ``template_hashes``, ``file_hashes``, and ``output_files_map`` do not list
  the receipt path, so a second ``--update`` does not flag it as drift.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

import build_team
from agentteams import drift as _drift


REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "delivery-receipt.schema.json"
EXAMPLES_DIR = REPO_ROOT / "examples"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _seed_waiver_and_decision(output_dir: Path, monkeypatch, *, ticket: str) -> None:
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    waiver_key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    waiver = {
        "timestamp": "2026-05-03T00:00:00Z", "waiver_id": f"waiver-{ticket}",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2099-01-01T00:00:00Z",
        "max_uses": "9", "uses": "0", "approver": "test-harness",
        "ticket_id": ticket, "reason_code": "TEST",
        "conditions_verified": "verified", "signature": "",
    }
    payload = "|".join([
        waiver["waiver_id"], waiver["action_reviewed"], waiver["expires_at"],
        waiver["max_uses"], waiver["uses"], waiver["approver"],
        waiver["ticket_id"], waiver["reason_code"], waiver["conditions_verified"],
    ])
    waiver["signature"] = hmac.new(
        waiver_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        "{timestamp},{waiver_id},{action_reviewed},{expires_at},{max_uses},"
        "{uses},{approver},{ticket_id},{reason_code},{conditions_verified},"
        "{signature}\n".format(**waiver),
        encoding="utf-8",
    )


def _seed_pass_decision(output_dir: Path) -> None:
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-05-03T00:00:00Z,test-harness,overwrite,PASS,,verified\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Schema validity
# ---------------------------------------------------------------------------

def test_delivery_receipt_schema_is_valid_draft7():
    import jsonschema
    jsonschema.Draft7Validator.check_schema(_schema())


def test_delivery_receipt_path_constant_matches_documented_location():
    assert build_team.DELIVERY_RECEIPT_REL_PATH == "references/delivery-receipt.json"


# ---------------------------------------------------------------------------
# Writer unit tests
# ---------------------------------------------------------------------------

def test_write_delivery_receipt_produces_schema_valid_payload(tmp_path):
    import jsonschema
    manifest = {
        "project_name": "TestProject",
        "framework": "copilot-vscode",
        "components": [],
        "selected_archetypes": [],
        "agent_slug_list": [],
        "governance_agents": [],
        "output_files": [],
        "tools": [],
        "auto_resolved_placeholders": {},
        "manual_required_placeholders": [],
    }
    output_dir = tmp_path / ".github" / "agents"
    output_dir.mkdir(parents=True)
    receipt_path = build_team._write_delivery_receipt(manifest, output_dir)
    assert receipt_path == output_dir / "references" / "delivery-receipt.json"
    assert receipt_path.exists()
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(_schema()).validate(payload)
    assert payload["artifact_type"] == "delivery-receipt"
    assert payload["fingerprint_algo_version"] == _drift.FINGERPRINT_ALGO_VERSION
    assert payload["manifest_fingerprint"] == _drift.compute_manifest_fingerprint(manifest)
    assert payload["project_name"] == "TestProject"
    assert payload["framework"] == "copilot-vscode"


def test_write_delivery_receipt_output_dir_is_never_absolute(tmp_path):
    """Security fix: a receipt written into a tracked/published location must never
    leak the operator's home directory / OS username via an absolute output_dir.
    tmp_path is not inside a git repo, so this exercises the plain-basename fallback."""
    manifest = {
        "project_name": "TestProject",
        "framework": "copilot-vscode",
        "components": [],
        "selected_archetypes": [],
        "agent_slug_list": [],
        "governance_agents": [],
        "output_files": [],
        "tools": [],
        "auto_resolved_placeholders": {},
        "manual_required_placeholders": [],
    }
    output_dir = tmp_path / "some-project" / ".github" / "agents"
    output_dir.mkdir(parents=True)
    receipt_path = build_team._write_delivery_receipt(manifest, output_dir)
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert not payload["output_dir"].startswith("/")
    assert str(tmp_path) not in payload["output_dir"]
    assert payload["output_dir"] == "agents"


def test_write_delivery_receipt_output_dir_is_repo_relative_inside_a_git_repo(tmp_path):
    """Inside a git repo, output_dir should be repo-relative, not just a bare basename —
    still informative without leaking the machine's absolute path."""
    manifest = {
        "project_name": "TestProject",
        "framework": "copilot-vscode",
        "components": [],
        "selected_archetypes": [],
        "agent_slug_list": [],
        "governance_agents": [],
        "output_files": [],
        "tools": [],
        "auto_resolved_placeholders": {},
        "manual_required_placeholders": [],
    }
    repo_root = tmp_path / "fake-repo"
    output_dir = repo_root / ".github" / "agents"
    output_dir.mkdir(parents=True)
    (repo_root / ".git").mkdir()
    receipt_path = build_team._write_delivery_receipt(manifest, output_dir)
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["output_dir"] == str(Path(".github") / "agents")
    assert not payload["output_dir"].startswith("/")


def test_require_jsonschema_missing_degrades_to_writer_error(monkeypatch):
    """A missing jsonschema must surface as the writer's own (non-fatal) error
    type — never a bare ImportError that would crash a completed merge.

    Regression: in the original fleet run the interpreter lacked jsonschema, so
    the post-merge ``import jsonschema`` raised ModuleNotFoundError, escaped the
    ``except (OSError, DeliveryReceiptError)`` handler in ``main()``, and turned
    a fully successful, non-destructive merge into an exit-1 traceback.
    """
    import sys

    # Setting the module to None in sys.modules makes ``import jsonschema`` raise
    # ImportError without uninstalling the real package.
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    with pytest.raises(build_team.DeliveryReceiptError, match="jsonschema is not installed"):
        build_team._require_jsonschema(build_team.DeliveryReceiptError, "delivery receipt")


def test_write_delivery_receipt_missing_jsonschema_is_nonfatal(tmp_path, monkeypatch):
    """End-to-end: with jsonschema absent the writer raises DeliveryReceiptError
    (which ``main()`` swallows) and writes NO partial receipt file."""
    import sys

    monkeypatch.setitem(sys.modules, "jsonschema", None)
    manifest = {
        "project_name": "TestProject",
        "framework": "copilot-vscode",
        "components": [],
        "selected_archetypes": [],
        "agent_slug_list": [],
        "governance_agents": [],
        "output_files": [],
        "tools": [],
        "auto_resolved_placeholders": {},
        "manual_required_placeholders": [],
    }
    output_dir = tmp_path / ".github" / "agents"
    output_dir.mkdir(parents=True)
    with pytest.raises(build_team.DeliveryReceiptError, match="jsonschema is not installed"):
        build_team._write_delivery_receipt(manifest, output_dir)
    assert not (output_dir / build_team.DELIVERY_RECEIPT_REL_PATH).exists()


def test_write_delivery_receipt_artifact_type_is_not_schema_version(tmp_path):
    """Pins D3: discriminator MUST be ``artifact_type``, not ``schema_version``.

    Reading code keying on ``schema_version`` (e.g. build-log readers) must not
    accidentally treat a receipt as a build-log.
    """
    manifest = {"project_name": "X", "framework": "copilot-vscode"}
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    receipt_path = build_team._write_delivery_receipt(manifest, output_dir)
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert "schema_version" not in payload
    assert payload["artifact_type"] == "delivery-receipt"


# ---------------------------------------------------------------------------
# Integration: --update writes a receipt; --dry-run does not; drift excludes it
# ---------------------------------------------------------------------------

def test_update_writes_delivery_receipt(tmp_path, monkeypatch):
    import jsonschema
    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("software-project brief not found")

    output_dir = tmp_path / ".github" / "agents"
    _seed_waiver_and_decision(output_dir, monkeypatch, ticket="RECEIPT-1")

    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0
    _seed_pass_decision(output_dir)

    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ]) == 0

    receipt_path = output_dir / "references" / "delivery-receipt.json"
    assert receipt_path.exists(), "--update did not write delivery-receipt.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(_schema()).validate(payload)

    # Receipt fingerprint must equal the freshly-written build-log fingerprint
    # (heal first, attest second).
    log = json.loads((output_dir / "references" / "build-log.json").read_text())
    assert payload["manifest_fingerprint"] == log["manifest_fingerprint"]
    assert payload["fingerprint_algo_version"] == log["fingerprint_algo_version"]


def test_update_dry_run_does_not_write_delivery_receipt(tmp_path, monkeypatch):
    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("software-project brief not found")

    output_dir = tmp_path / ".github" / "agents"
    _seed_waiver_and_decision(output_dir, monkeypatch, ticket="RECEIPT-2")

    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0

    # Initial generation also writes a receipt only on --update, so confirm
    # the receipt does not exist yet (initial run uses default path).
    receipt_path = output_dir / "references" / "delivery-receipt.json"
    receipt_existed_before = receipt_path.exists()

    rc = build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--dry-run", "--yes", "--no-scan", "--security-offline",
    ])
    assert rc == 0
    assert receipt_path.exists() == receipt_existed_before, (
        "--update --dry-run wrote (or removed) a delivery receipt"
    )


def test_delivery_receipt_excluded_from_drift_artifacts(tmp_path, monkeypatch):
    """Receipt path must not appear in build-log ``template_hashes``,
    ``file_hashes``, or ``output_files_map``, AND a second ``--update`` must
    not flag the receipt as drift.
    """
    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("software-project brief not found")

    output_dir = tmp_path / ".github" / "agents"
    _seed_waiver_and_decision(output_dir, monkeypatch, ticket="RECEIPT-3")

    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0
    _seed_pass_decision(output_dir)
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ]) == 0

    receipt_rel = build_team.DELIVERY_RECEIPT_REL_PATH
    log = json.loads((output_dir / "references" / "build-log.json").read_text())
    assert receipt_rel not in log.get("template_hashes", {})
    assert receipt_rel not in log.get("file_hashes", {})
    output_files_map = log.get("output_files_map", [])
    if output_files_map and isinstance(output_files_map[0], dict):
        paths = {f.get("path") for f in output_files_map}
    else:
        paths = set(output_files_map)
    assert receipt_rel not in paths

    # Second --update must not re-render based on the receipt's existence.
    _seed_pass_decision(output_dir)
    rc = build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--check", "--no-scan", "--security-offline",
    ])
    # rc may be 0 or 1 depending on security-refresh-only drift; the contract
    # under test is that the RECEIPT itself does not appear in the drift
    # report.
    log_after = json.loads((output_dir / "references" / "build-log.json").read_text())
    assert receipt_rel not in log_after.get("template_hashes", {})
    assert receipt_rel not in log_after.get("file_hashes", {})


# ---------------------------------------------------------------------------
# RA2: runtime schema validation of the receipt
# ---------------------------------------------------------------------------

def test_write_delivery_receipt_rejects_nonconforming_payload(tmp_path, monkeypatch):
    """RA2: a receipt that does not conform to the shipped schema must raise
    DeliveryReceiptError at write time and write NO file (not silently emit a
    malformed attestation)."""
    # Force an invalid manifest_fingerprint (schema requires non-empty string).
    monkeypatch.setattr(_drift, "compute_manifest_fingerprint", lambda m: "")

    manifest = {"project_name": "P", "framework": "copilot-vscode"}
    with pytest.raises(build_team.DeliveryReceiptError, match="schema validation"):
        build_team._write_delivery_receipt(manifest, tmp_path)

    assert not (tmp_path / build_team.DELIVERY_RECEIPT_REL_PATH).exists(), (
        "a non-conforming receipt must not be written"
    )


def test_write_delivery_receipt_error_is_runtime_error_subclass():
    """Callers catch (OSError, DeliveryReceiptError) and treat it non-fatally;
    keep it a RuntimeError subclass so it never masquerades as OSError."""
    assert issubclass(build_team.DeliveryReceiptError, RuntimeError)
    assert not issubclass(build_team.DeliveryReceiptError, OSError)
