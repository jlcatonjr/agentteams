"""
security_gate.py — destructive-action security decision/waiver gate.

Extracted verbatim from build_team.py with no logic change (CH-07 modular
structure). This gate is fail-CLOSED: every unresolved path raises (== deny),
and it already follows CH-24 (narrow catch -> contextual re-raise). It is
EXEMPT from the CH-24 exception sweep — its pattern is the target state, not a
defect. build_team re-exports these names so callers (main) and tests resolve
them in build_team's namespace unchanged.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import os
from datetime import UTC, datetime
from pathlib import Path

# --- migrate destructive-gate exemption -----------------------------------
# Homed here (not in build_team) so the WRITER (_run_migrate) and the READER
# (main) share one source of truth even after main moves to cli/app.py. Set
# ONLY by _run_migrate around its main() re-invocation — not reachable from the
# CLI — so the bypass stays internal. (Audit: a build_team module global would
# split reader/writer across modules when main moves and silently break this.)
_migrate_exemption_active = False


def set_migrate_exemption(active: bool) -> None:
    """Enable/disable the --migrate overwrite-gate exemption (internal use only)."""
    if not isinstance(active, bool):
        raise TypeError(f"active must be bool, got {type(active).__name__}")
    global _migrate_exemption_active
    _migrate_exemption_active = active


def migrate_exemption_active() -> bool:
    """True when the internal --migrate destructive-gate exemption is active."""
    return _migrate_exemption_active


_SECURITY_DECISION_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "legacy": frozenset(
        {
            "timestamp",
            "requesting_agent",
            "action_reviewed",
            "verdict",
            "conditions",
            "conditions_verified",
        }
    ),
    "current": frozenset(
        {
            "date",
            "plan_slug",
            "step",
            "decision",
            "status",
            "conditions",
            "conditions_verified",
            "evidence",
            "owner",
        }
    ),
}

_SECURITY_WAIVER_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {
        "timestamp",
        "waiver_id",
        "action_reviewed",
        "expires_at",
        "max_uses",
        "uses",
        "approver",
        "ticket_id",
        "reason_code",
        "conditions_verified",
        "signature",
    }
)

_SECURITY_INTEL_TTL_HOURS = 24


def _assert_destructive_action_allowed(output_dir: Path, *, action: str) -> None:
    """Raise RuntimeError if security decisions do not allow destructive action.

    The check follows documented security protocol semantics:
    - HALT blocks execution
    - CONDITIONAL PASS requires conditions_verified=verified
    - PASS allows execution
    - No matching decision blocks execution
    """
    decision = _latest_security_decision(output_dir, action=action)
    if decision is None:
        waiver = _latest_security_waiver(output_dir, action=action)
        if waiver is not None:
            _consume_security_waiver_use(output_dir, waiver, action=action)
            return
        raise RuntimeError(
            "no matching PASS decision found in references/security-decisions.log.csv"
        )

    verdict = decision.get("verdict", "").strip().upper()
    cond_verified = decision.get("conditions_verified", "").strip().lower()
    action_reviewed = decision.get("action_reviewed", "").strip()

    if verdict == "HALT":
        raise RuntimeError(
            f"latest decision for action '{action_reviewed or action}' is HALT"
        )

    waiver = _latest_security_waiver(output_dir, action=action)
    if waiver is not None:
        _consume_security_waiver_use(output_dir, waiver, action=action)
        return

    if verdict == "PASS":
        _consume_security_decision_use(output_dir, decision, action=action)
        return

    if verdict == "CONDITIONAL PASS" and cond_verified == "verified":
        _consume_security_decision_use(output_dir, decision, action=action)
        return

    if verdict == "USED":
        raise RuntimeError(
            "no matching PASS decision found in references/security-decisions.log.csv"
        )

    if verdict == "CONDITIONAL PASS":
        raise RuntimeError(
            "latest CONDITIONAL PASS has unverified conditions "
            f"(conditions_verified={cond_verified or 'pending'})"
        )

    if verdict not in {"PASS", "CONDITIONAL PASS"}:
        raise RuntimeError(
            f"latest decision has unsupported verdict '{verdict or 'UNKNOWN'}'"
        )

    raise RuntimeError(
        "no matching PASS decision found in references/security-decisions.log.csv"
    )


def _consume_security_decision_use(output_dir: Path, decision: dict[str, str], *, action: str) -> None:
    """Mark a validated security decision as consumed so it cannot be replayed."""
    log_path = output_dir / "references" / "security-decisions.log.csv"
    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to update security decision log: {exc}") from exc

    if not rows:
        raise RuntimeError("security decision log is empty")

    target_action = decision.get("action_reviewed", "").strip()
    target_timestamp = decision.get("timestamp", "").strip()
    target_verdict = decision.get("verdict", "").strip().upper()

    updated = False
    for row in reversed(rows):
        row_action = row.get("action_reviewed", row.get("decision", "")).strip()
        if row_action != target_action:
            continue
        row_timestamp = row.get("timestamp", row.get("date", "")).strip()
        if target_timestamp and row_timestamp != target_timestamp:
            continue
        row_verdict = row.get("verdict", row.get("status", row.get("decision", ""))).strip().upper()
        if row_verdict != target_verdict:
            continue

        if "consumed" not in fieldnames:
            fieldnames.append("consumed")
        row["consumed"] = "yes"
        updated = True
        break

    if not updated:
        raise RuntimeError(f"validated decision for action '{action}' could not be updated")

    try:
        with log_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as exc:
        raise RuntimeError(f"unable to persist security decision log: {exc}") from exc


def _assert_security_intelligence_fresh(
    security_placeholders: dict[str, str],
    *,
    output_dir: Path,
) -> None:
    """Raise RuntimeError when generated security intelligence is stale."""
    freshness = _security_intelligence_freshness(security_placeholders)
    if freshness["status"] == "fresh":
        return

    waiver = _latest_security_waiver(output_dir, action="security-intel-freshness")
    if waiver is not None:
        _consume_security_waiver_use(output_dir, waiver, action="security-intel-freshness")
        return

    raise RuntimeError(
        "security intelligence is stale "
        f"(status={freshness['status']}, age_hours={freshness['age_hours']}, "
        f"ttl_hours={freshness['ttl_hours']})"
    )


def _security_intelligence_freshness(security_placeholders: dict[str, str]) -> dict[str, str]:
    """Return machine-readable freshness state for generated security intelligence."""
    explicit_status = security_placeholders.get("SECURITY_DATA_FRESHNESS_STATUS", "").strip().lower()
    generated_at = security_placeholders.get("SECURITY_DATA_GENERATED_AT", "")
    summary = security_placeholders.get("SECURITY_CURRENT_THREATS_SUMMARY", "")
    playbook = security_placeholders.get("SECURITY_PREVENTION_PLAYBOOK", "")

    age_hours = ""
    status = "unknown"
    if explicit_status in {"fresh", "stale", "unknown"}:
        status = explicit_status
    try:
        generated_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        age_delta = datetime.now(UTC) - generated_dt
        age_hours_raw = age_delta.total_seconds() / 3600.0
        if age_hours_raw < -(5.0 / 60.0):
            age_hours = f"{age_hours_raw:.2f}"
            status = "stale"
        else:
            age_hours_value = max(age_hours_raw, 0.0)
            age_hours = f"{age_hours_value:.2f}"
            if age_hours_value <= _SECURITY_INTEL_TTL_HOURS and "STALE DATA" not in summary and "STALE DATA" not in playbook and explicit_status != "stale":
                status = "fresh"
            else:
                status = "stale"
    except ValueError:
        status = "stale"

    return {
        "status": status,
        "age_hours": age_hours,
        "ttl_hours": str(_SECURITY_INTEL_TTL_HOURS),
    }


def _consume_security_waiver_use(output_dir: Path, waiver: dict[str, str], *, action: str) -> None:
    """Increment the use counter for an already validated security waiver."""
    log_path = output_dir / "references" / "security-waivers.log.csv"
    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to update security waiver log: {exc}") from exc

    if not rows:
        raise RuntimeError("security waiver log is empty")

    target_id = waiver.get("waiver_id", "").strip()
    if not target_id:
        raise RuntimeError("validated waiver is missing waiver_id")

    signing_key = os.getenv("AGENTTEAMS_WAIVER_SIGNING_KEY", "")
    if not signing_key:
        raise RuntimeError("waiver signing key is not configured")

    updated = False
    for row in reversed(rows):
        if (row.get("waiver_id", "").strip() != target_id) or not _action_matches(row.get("action_reviewed", ""), action):
            continue

        try:
            uses_value = int((row.get("uses", "") or "0").strip() or 0)
        except ValueError as exc:
            raise RuntimeError("waiver use counters are not numeric") from exc
        row["uses"] = str(uses_value + 1)
        payload = "|".join(
            [
                row.get("waiver_id", "").strip(),
                row.get("action_reviewed", "").strip(),
                row.get("expires_at", "").strip(),
                row.get("max_uses", "").strip(),
                row.get("uses", "").strip(),
                row.get("approver", "").strip(),
                row.get("ticket_id", "").strip(),
                row.get("reason_code", "").strip(),
                row.get("conditions_verified", "").strip(),
            ]
        )
        row["signature"] = hmac.new(
            signing_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        updated = True
        break

    if not updated:
        raise RuntimeError(f"validated waiver '{target_id}' could not be updated")

    try:
        with log_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as exc:
        raise RuntimeError(f"unable to persist security waiver log: {exc}") from exc


def _latest_security_waiver(output_dir: Path, *, action: str) -> dict[str, str] | None:
    """Return the latest valid security waiver for an action keyword, if present."""
    log_path = output_dir / "references" / "security-waivers.log.csv"
    if not log_path.exists():
        return None

    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            actual_columns = [c.strip() for c in (reader.fieldnames or [])]
            _security_waiver_schema_kind(actual_columns)
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to read security waiver log: {exc}") from exc

    if not rows:
        return None

    for row in reversed(rows):
        reviewed_candidates = [
            row.get("action_reviewed") or "",
        ]
        if not any(_action_matches(candidate, action) for candidate in reviewed_candidates):
            continue

        normalized_row = {k: (v or "") for k, v in row.items()}
        _validate_security_waiver(normalized_row, action=action)
        return normalized_row
    return None


def _security_waiver_schema_kind(actual_columns: list[str]) -> str:
    """Return the supported schema kind for a security-waiver log header."""
    normalized = [c.strip() for c in actual_columns]
    if _SECURITY_WAIVER_REQUIRED_COLUMNS.issubset(normalized):
        return "waiver"
    raise RuntimeError(
        "security waiver log is malformed: expected header "
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,ticket_id,reason_code,conditions_verified,signature"
    )


def _validate_security_waiver(waiver: dict[str, str], *, action: str) -> None:
    """Raise RuntimeError if a waiver row is missing required properties."""
    if not _action_matches(waiver.get("action_reviewed", ""), action):
        raise RuntimeError(f"waiver scope mismatch for action '{action}'")

    if waiver.get("conditions_verified", "").strip().lower() != "verified":
        raise RuntimeError("waiver conditions are not verified")

    approver = waiver.get("approver", "").strip()
    ticket_id = waiver.get("ticket_id", "").strip()
    reason_code = waiver.get("reason_code", "").strip()
    if not approver or not ticket_id or not reason_code:
        raise RuntimeError("waiver is missing approver, ticket_id, or reason_code")

    try:
        expires_at = datetime.fromisoformat(waiver.get("expires_at", "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("waiver expires_at is not a valid ISO-8601 timestamp") from exc
    if expires_at <= datetime.now(UTC):
        raise RuntimeError("waiver has expired")

    try:
        max_uses = int(waiver.get("max_uses", "0") or 0)
        uses = int(waiver.get("uses", "0") or 0)
    except ValueError as exc:
        raise RuntimeError("waiver use counters are not numeric") from exc
    if max_uses <= 0 or uses >= max_uses:
        raise RuntimeError("waiver use limit has been reached")

    signing_key = os.getenv("AGENTTEAMS_WAIVER_SIGNING_KEY", "")
    if not signing_key:
        raise RuntimeError("waiver signing key is not configured")

    payload = "|".join(
        [
            waiver.get("waiver_id", "").strip(),
            waiver.get("action_reviewed", "").strip(),
            waiver.get("expires_at", "").strip(),
            waiver.get("max_uses", "").strip(),
            waiver.get("uses", "").strip(),
            approver,
            ticket_id,
            reason_code,
            waiver.get("conditions_verified", "").strip(),
        ]
    )
    expected_signature = hmac.new(
        signing_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, waiver.get("signature", "").strip().lower()):
        raise RuntimeError("waiver signature verification failed")


def _latest_security_decision(output_dir: Path, *, action: str) -> dict[str, str] | None:
    """Return the latest security decision row matching an action keyword."""
    log_path = output_dir / "references" / "security-decisions.log.csv"
    if not log_path.exists():
        return None

    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            actual_columns = [c.strip() for c in (reader.fieldnames or [])]
            schema_kind = _security_decision_schema_kind(actual_columns)
            action_field = "action_reviewed" if schema_kind == "legacy" else "decision"
            verdict_field = "verdict" if schema_kind == "legacy" else "status"
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to read security decisions log: {exc}") from exc

    if not rows:
        return None

    for row in reversed(rows):
        if row.get("consumed", "").strip().lower() in {"yes", "true", "1"}:
            continue
        reviewed_candidates = [
            row.get(action_field) or "",
            row.get("action_reviewed") or "",
            row.get("decision") or "",
        ]
        if any(_action_matches(candidate, action) for candidate in reviewed_candidates):
            normalized_row = {k: (v or "") for k, v in row.items()}
            normalized_row["action_reviewed"] = normalized_row.get(action_field, normalized_row.get("action_reviewed", ""))
            normalized_row["verdict"] = normalized_row.get(verdict_field, normalized_row.get("verdict", ""))
            normalized_row["timestamp"] = normalized_row.get("timestamp", normalized_row.get("date", ""))
            return normalized_row
    return None


def _security_decision_schema_kind(actual_columns: list[str]) -> str:
    """Return the supported schema kind for a security-decision log header.

    Accepts either the legacy six-column schema or the current repository schema
    with additional provenance fields. The required subset must be present.
    """
    normalized = [c.strip() for c in actual_columns]
    for schema_kind, required in _SECURITY_DECISION_REQUIRED_COLUMNS.items():
        if required.issubset(normalized):
            return schema_kind
    raise RuntimeError(
        "security decisions log is malformed: expected either the legacy header "
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified "
        "or the current repository header date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner"
    )


def _action_matches(action_reviewed: str, action: str) -> bool:
    """Return True for strict action-id style matches.

    Accepted patterns:
    - <action>
    - <action>-<suffix>
    - <action>_<suffix>
    - <action>.<suffix>
    - <action>:<suffix>
    """
    action_norm = action.strip().lower()
    reviewed_norm = action_reviewed.strip().lower()
    if not action_norm:
        return False
    if reviewed_norm == action_norm:
        return True
    return reviewed_norm.startswith(
        (f"{action_norm}-", f"{action_norm}_", f"{action_norm}.", f"{action_norm}:")
    )
