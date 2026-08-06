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
import warnings
from datetime import UTC, datetime
from pathlib import Path

# --- migrate destructive-gate exemption -----------------------------------
#
# REMOVED 2026-08-06 (audit W4 / probe A12). This was a module-level boolean plus a public
# setter, and `generate.py` read it to decide whether to SKIP the destructive-action gate
# entirely. Its docstring said the bypass was "not reachable from the CLI", which was true and
# beside the point: `set_migrate_exemption(True)` was importable by anything running in the
# process, so the gate could be disabled by any code that could `import`. A security control
# whose off-switch is a public module attribute has an off-switch, not a control.
#
# The exemption itself is legitimate — `--migrate` supplies its own rollback (the
# pre-fencing-snapshot git tag) — so it survives as an explicit parameter threaded from
# `_run_migrate` through `main()` to `run_generate()`. Ambient process state became an
# argument, which is the whole fix: an argument cannot be set by an unrelated importer.


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

# Ordered fields the waiver HMAC-SHA256 signs, pipe-joined (excludes `timestamp`
# and `signature`). Single source of truth (CH-05) for both _validate_security_waiver
# and _consume_security_waiver_use; the docs' manual-mint procedure is pinned to
# this tuple by tests/test_security_waiver_docs.py.
_WAIVER_SIGNATURE_FIELDS: tuple[str, ...] = (
    "waiver_id",
    "action_reviewed",
    "expires_at",
    "max_uses",
    "uses",
    "approver",
    "ticket_id",
    "reason_code",
    "conditions_verified",
)

_SECURITY_INTEL_TTL_HOURS = 24

# Decision-log authentication lives in decision_log.py (CH-07 carve). Re-exported so every
# existing `from agentteams.cli.security_gate import ...` keeps resolving.
from agentteams.cli.decision_log import (  # noqa: E402,F401
    _SECURITY_DECISION_REQUIRED_COLUMNS,
    _action_matches,
    _scope_permits,
    scope_suffix_warning,
    _security_decision_schema_kind,
    _DECISION_SIGNATURE_FIELDS,
    _DEFAULT_DECISION_AUTHORS,
    _HALT_RETRACTION_VERDICT,
    _approved_decision_authors,
    _assert_authorizing_row_is_authentic,
    _assert_decision_chain_intact,
    _assert_no_unretracted_halt,
    _decision_author,
    _decision_signature_payload,
    _decision_signing_active,
    _read_decision_rows,
    sign_decision_row,
)


def check_clearance(output_dir: Path, *, action: str) -> tuple[bool, str]:
    """Evaluate the gate for *action* WITHOUT consuming anything. Read-only.

    ``_assert_destructive_action_allowed`` marks the decision or waiver it accepts as used —
    correct for enforcement, and a trap for inspection: asking "would this be allowed?" by
    calling it spends the clearance and rewrites the log. During this remediation that trap
    fired on the live repository's own decisions log, adding a ``consumed`` column and burning
    a real PASS row. ``verify_waivers`` already exists for exactly this reason on the waiver
    side; this is its counterpart for decisions.

    Args:
        output_dir: The generated team's output directory.
        action: The destructive action id, e.g. ``"overwrite"``.

    Returns:
        ``(allowed, reason)``. ``reason`` is empty when allowed, otherwise the refusal message.
    """
    try:
        _assert_destructive_action_allowed(output_dir, action=action, consume=False)
        return True, ""
    except RuntimeError as exc:
        return False, str(exc)


def _assert_destructive_action_allowed(
    output_dir: Path, *, action: str, consume: bool = True
) -> None:
    """Raise RuntimeError if security decisions do not allow destructive action.

    The check follows documented security protocol semantics:
    - An unretracted HALT anywhere in the log blocks execution (C-2), and no waiver passes it
    - CONDITIONAL PASS requires conditions_verified=verified
    - PASS allows execution
    - No matching decision blocks execution
    - An authorizing row must be issued by an approved security author, and must carry a
      valid signature when signing is active

    Args:
        output_dir: The generated team's output directory.
        action: The destructive action id.
        consume: When True (the default, and what every enforcement caller uses) the accepted
            decision or waiver is marked used so it cannot be replayed. :func:`check_clearance`
            passes False to inspect without spending. Keeping this as one flag on one function
            rather than a parallel implementation is deliberate — two copies of a security
            decision procedure drift, and the copy that drifts is the one nobody runs.
    """
    # C-2 first, and over the WHOLE log. Everything below chooses among clearances; this
    # decides whether any clearance may apply at all.
    _assert_no_unretracted_halt(output_dir, action=action)

    decision = _latest_security_decision(output_dir, action=action)
    if decision is None:
        waiver = _latest_security_waiver(output_dir, action=action)
        if waiver is not None:
            if consume:
                _consume_security_waiver_use(output_dir, waiver, action=action)
            return
        raise RuntimeError(
            "no matching PASS decision found in references/security-decisions.log.csv"
        )

    verdict = decision.get("verdict", "").strip().upper()
    cond_verified = decision.get("conditions_verified", "").strip().lower()
    action_reviewed = decision.get("action_reviewed", "").strip()

    if verdict == "HALT":
        # Unreachable in practice — _assert_no_unretracted_halt already raised. Kept as a
        # belt-and-braces guard so a future refactor that reorders these calls fails closed.
        raise RuntimeError(
            f"latest decision for action '{action_reviewed or action}' is HALT"
        )

    waiver = _latest_security_waiver(output_dir, action=action)
    if waiver is not None:
        if consume:
            _consume_security_waiver_use(output_dir, waiver, action=action)
        return

    if verdict in {"PASS", "CONDITIONAL PASS"}:
        read = _read_decision_rows(output_dir)
        signing_active = _decision_signing_active(read[1] if read else [])
        _assert_authorizing_row_is_authentic(
            decision, output_dir=output_dir, signing_active=signing_active, action=action
        )
        # W11: a suffix that reads as a restriction and is not one. Advisory — see
        # decision_log.scope_suffix_warning for why this warns rather than refuses.
        warning = scope_suffix_warning(decision, action)
        if warning:
            warnings.warn(warning, stacklevel=2)

    if verdict == "PASS":
        if consume:
            _consume_security_decision_use(output_dir, decision, action=action)
        return

    if verdict == "CONDITIONAL PASS" and cond_verified == "verified":
        if consume:
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


#: Placeholder carrying the SHA-256 of the intelligence payload the timestamp describes.
#: Absent on payloads produced before 2026-08-06, which is why verification is conditional.
_INTEL_DIGEST_PLACEHOLDER = "SECURITY_DATA_PAYLOAD_DIGEST"


def security_intelligence_digest(security_placeholders: dict[str, str]) -> str:
    """Return the SHA-256 of the intel-bearing content a freshness claim covers.

    Computed over the intel-bearing placeholders in a fixed order, so the digest is a function
    of the DATA and not of dict ordering or of the timestamp being asserted about it.
    """
    parts = [
        f"{key}={security_placeholders.get(key, '')}"
        for key in sorted(_INTEL_BEARING_PLACEHOLDERS)
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _assert_intelligence_digest_matches(security_placeholders: dict[str, str]) -> None:
    """Raise when a declared payload digest does not match the payload being interpolated.

    The freshness gate authenticates a TIMESTAMP STRING, not the data it describes: rewriting
    ``SECURITY_DATA_GENERATED_AT`` to now relabels a months-old snapshot as fresh and the gate
    passes (audit W10 / probe A10). Binding the timestamp to a digest of the payload closes
    that: relabelling now requires also regenerating the digest, which means actually having
    the data.

    Conditional by construction — a payload with no digest cannot be verified, and refusing
    every pre-2026-08-06 cache would break offline runs the gate is meant to permit. The
    residual gap is stated rather than hidden: until producers emit the digest, this is a
    ratchet on new payloads, not a guarantee about old ones.
    """
    declared = (security_placeholders.get(_INTEL_DIGEST_PLACEHOLDER) or "").strip().lower()
    if not declared:
        return
    actual = security_intelligence_digest(security_placeholders)
    if not hmac.compare_digest(declared, actual):
        raise RuntimeError(
            "security intelligence payload digest mismatch: the freshness claim describes a "
            f"different payload than the one being interpolated (declared {declared[:12]}..., "
            f"computed {actual[:12]}...). A timestamp was changed without the data changing."
        )


def _assert_security_intelligence_fresh(
    security_placeholders: dict[str, str],
    *,
    output_dir: Path,
) -> None:
    """Raise RuntimeError when generated security intelligence is stale."""
    _assert_intelligence_digest_matches(security_placeholders)
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
        f"ttl_hours={freshness['ttl_hours']}). "
        f"{_stale_intel_blast_radius(security_placeholders)} "
        "No 'security-intel-freshness' waiver was found. For air-gapped/offline runs, "
        "add a signed waiver (see docs/security-hardening-guide; check it with "
        "`agentteams --verify-waivers`). Note: --security-offline does NOT apply to "
        "cross-framework operations (bridge/convert/interop) — they require live intel."
    )


#: Placeholder keys whose rendered value embeds time-sensitive threat intelligence. A file that
#: interpolates none of these carries no stale data even when the snapshot is past its TTL.
#:
#: **These must be keys ``security_refs.build_security_placeholders`` actually returns.** They
#: were not. Until 2026-08-06 this tuple named ``THREAT_INTELLIGENCE``,
#: ``SECURITY_VULNERABILITY_WATCH``, ``KEV_CATALOG``, ``EPSS_SCORES`` and ``CVE_SUMMARY`` — none
#: of which the producer emits, an overlap of exactly zero. Two things followed:
#:
#: * :func:`_stale_intel_blast_radius` took its "no intel-bearing placeholder is populated"
#:   branch on **every** run, so the sentence an operator read when the freshness gate refused
#:   was always the wrong one;
#: * :func:`security_intelligence_digest` hashed the same names and therefore returned a
#:   CONSTANT — the digest of a fully-populated payload equalled the digest of ``{}``.
#:
#: The second is why the divergence is now pinned by a test
#: (``tests/test_security_intel_digest.py``) rather than by this comment: a verification that
#: passes for every input is worse than none, because it reads as coverage.
_INTEL_BEARING_PLACEHOLDERS: tuple[str, ...] = (
    "SECURITY_CURRENT_THREATS_SUMMARY",
    "SECURITY_PREVENTION_PLAYBOOK",
    "SECURITY_LLM_THREATS_SUMMARY",
    "SECURITY_OSV_PACKAGES_SUMMARY",
    "SECURITY_VULNERABILITY_WATCH_JSON",
    "SECURITY_SOURCE_REGISTRY",
)


def _stale_intel_blast_radius(security_placeholders: dict[str, str]) -> str:
    """Describe how much of a run stale intelligence actually affects.

    The gate is all-or-nothing: one stale snapshot fails the whole run, including files with no
    security content at all. Operators hitting this could not tell "the intel is load-bearing
    here" from "an unrelated reference file is being held up by a cache timestamp", and the
    logged workaround was to bypass the CLI entirely and drive ingest/analyze/render/emit by hand
    — which skips *every* gate, not just this one.

    This does **not** narrow the gate. Scoping a security control is an operator decision, not a
    convenience one: a team whose security agent quotes expired advisories is a real hazard even
    when the file being written this minute is innocuous. What it does is make the trade-off
    visible at the moment of refusal, so the operator can judge it.

    Args:
        security_placeholders: The resolved security placeholder map for this run.

    Returns:
        A sentence naming how many intel-bearing placeholders are in play.
    """
    bearing = [k for k in _INTEL_BEARING_PLACEHOLDERS if security_placeholders.get(k)]
    if not bearing:
        return (
            "Blast radius: no intel-bearing placeholder is populated for this run, so the "
            "staleness is unlikely to reach the generated files — the gate still refuses, "
            "because a team carrying expired advisories is a hazard regardless of which file "
            "is written this minute."
        )
    return (
        f"Blast radius: {len(bearing)} intel-bearing placeholder(s) would be interpolated "
        f"({', '.join(bearing)}), so generated content would embed the stale snapshot."
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
        payload = "|".join(row.get(f, "").strip() for f in _WAIVER_SIGNATURE_FIELDS)
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
        _validate_security_waiver(normalized_row, action=action, output_dir=output_dir)
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


def _validate_security_waiver(
    waiver: dict[str, str], *, action: str, output_dir: Path | None = None
) -> None:
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

    # W12: until 2026-08-06 these three were checked for non-emptiness and nothing else, so a
    # waiver could name any approver at all and pass — probe A9 minted one with
    # `approver=attacker, ticket_id=NONE` and the gate cleared it. Validating against the same
    # roster the decision path uses makes the field mean something.
    #
    # This does NOT close A9. A T2 attacker who owns the environment owns the signing key, and
    # a roster is text in the same tree. What it closes is the sub-finding: that the fields were
    # decorative.
    if output_dir is not None:
        approved = {a.lower().lstrip("@") for a in _approved_decision_authors(output_dir)}
        if approver.lower().lstrip("@") not in approved:
            raise RuntimeError(
                f"waiver approver {approver!r} is not on the approved roster ({sorted(approved)}); "
                f"see references/security-approvers.txt"
            )

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

    payload = "|".join(waiver.get(f, "").strip() for f in _WAIVER_SIGNATURE_FIELDS)
    expected_signature = hmac.new(
        signing_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, waiver.get("signature", "").strip().lower()):
        raise RuntimeError("waiver signature verification failed")


def verify_waivers(output_dir: Path) -> list[dict[str, str]]:
    """Read-only report of every waiver in the waiver log.

    Validates each row WITHOUT consuming it — never calls _consume_security_waiver_use
    (which increments `uses` and rewrites the CSV). Returns one dict per row with keys
    ``waiver_id``/``action``/``status``/``detail``. A missing log yields ``[]``. A row
    that fails any check (signature, expiry, use-limit, conditions, or an unset signing
    key) is reported ``status="invalid"`` with the reason in ``detail`` — never raised.
    """
    log_path = output_dir / "references" / "security-waivers.log.csv"
    if not log_path.exists():
        return []
    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to read security waiver log: {exc}") from exc

    results: list[dict[str, str]] = []
    for row in rows:
        normalized = {k: (v or "") for k, v in row.items()}
        action = normalized.get("action_reviewed", "").strip()
        try:
            _validate_security_waiver(normalized, action=action, output_dir=output_dir)
            status, detail = "valid", ""
        except RuntimeError as exc:
            status, detail = "invalid", str(exc)
        results.append({
            "waiver_id": normalized.get("waiver_id", "").strip(),
            "action": action,
            "status": status,
            "detail": detail,
        })
    return results


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
        # A HALT-RETRACTION is a record that a block was lifted, not a clearance to act. It is
        # consumed by _assert_no_unretracted_halt; selecting it here would surface it as "the
        # latest decision" and refuse with 'unsupported verdict'.
        row_verdict = (row.get(verdict_field) or row.get("verdict") or "").strip().upper()
        if row_verdict == _HALT_RETRACTION_VERDICT:
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
