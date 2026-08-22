"""decision_log.py — authentication of the security-decisions log.

Carved from ``security_gate`` (CH-07) when the hardening below pushed that module to 977 of the
1000-line ceiling. The seam is not arbitrary: everything here answers one question —
*may this row authorize anything?* — and none of it needs the waiver machinery, the freshness
gate, or the intel placeholders that make up the rest of ``security_gate``.

``security_gate`` re-exports every name, so no existing import or test changed.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import re
from pathlib import Path


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

    **The suffix decorates; it never narrows.** ``prune-001`` is an instance id, and matching
    it to the ``prune`` action is the intended behaviour. But the same field gets written as a
    *scope descriptor* — ``overwrite-single-readme-only`` — and this matcher cannot tell the
    two readings apart, so a clearance that reads as narrow grants the unrestricted action
    (audit W11 / probe A5). Expressing a real restriction requires the ``scope`` column, which
    :func:`_scope_permits` enforces; this function deliberately stays a pure id match, because
    inferring intent from a suffix is exactly the ambiguity that caused the finding.
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


#: An `action_reviewed` suffix that reads as an INSTANCE ID rather than a scope restriction:
#: `prune-001`, `overwrite-3`, `restore-backup.2`. Digits only, so anything wordy falls through
#: to being treated as a (non-functional) scope descriptor.
_INSTANCE_ID_SUFFIX_RE = re.compile(r"^[-_.:]\d+$")


def scope_suffix_warning(row: dict[str, str], action: str) -> str | None:
    """Return a warning when an `action_reviewed` suffix reads as a scope but restricts nothing.

    `action_reviewed` is overloaded. `prune-001` is an instance id and matching it to the
    `prune` action is intended. `overwrite-single-readme-only` reads as a *restriction*, and is
    not one — :func:`_action_matches` cannot distinguish the two, so the narrow-looking
    clearance grants the unrestricted action (audit W11 / probe A5).

    The `scope` column exists to express a real restriction. This says so at the moment the
    false belief would otherwise take effect.

    **Advisory, never a refusal.** Refusing would break every deployed log using a descriptive
    suffix, and the suffix is not itself dangerous — the belief about it is. This is also the
    one heuristic in this module, and CH-30 (no exemption without provenance) is deliberately
    not violated by it: CH-30 governs suppressions that let something *through*. A
    false positive here costs one line of output and can never permit an action.

    Args:
        row: The decision row being applied.
        action: The destructive action id being requested.

    Returns:
        A warning string, or None when the suffix is an instance id, absent, or the row already
        carries an explicit `scope`.
    """
    if (row.get("scope") or "").strip():
        return None
    reviewed = (row.get("action_reviewed") or row.get("decision") or "").strip()
    suffix = reviewed[len(action):] if reviewed.lower().startswith(action.lower()) else ""
    if not suffix or _INSTANCE_ID_SUFFIX_RE.match(suffix):
        return None
    return (
        f"security decision {reviewed!r} reads as a scoped clearance, but the suffix "
        f"{suffix!r} restricts nothing — it clears the unrestricted '{action}' action. To "
        f"actually narrow it, add a `scope` column listing the permitted action ids."
    )


def _scope_permits(row: dict[str, str], action: str) -> bool:
    """True when a decision row's explicit ``scope`` column admits *action*.

    The gate had no way to express "cleared for this action, but only in this narrow case".
    Operators reached for the ``action_reviewed`` suffix instead, which does not restrict
    anything (see :func:`_action_matches`). This adds the missing column:

    * **absent or empty** — no scope restriction, current behaviour, existing logs unaffected;
    * **present** — a semicolon-separated list of action ids, and the requested action must
      appear in it exactly. A scope that does not list the action refuses, so a narrowing
      written here actually narrows.

    Args:
        row: The decision row.
        action: The destructive action id being requested.

    Returns:
        Whether the row's scope admits the action.
    """
    scope = (row.get("scope") or "").strip()
    if not scope:
        return True
    permitted = {part.strip().lower() for part in scope.split(";") if part.strip()}
    return action.strip().lower() in permitted


# --- decision-log authentication (audit W2 / probes A1, A3, A4) ---------------
#
# The decisions log is the gate's ONLY authority for a destructive action, and until
# 2026-08-06 it was an unsigned CSV that any agent holding Write could author. Three
# breaches followed from that, all measured:
#
#   A1  a HALT was superseded by appending a later PASS row for the same action;
#   A3  a HALT was neutralised by setting its `consumed` column, the gate's own replay flag;
#   A4  the agent requesting the action wrote its own PASS row, and the gate cleared it.
#
# The three fixes below are deliberately asymmetric, because the two directions carry
# different risk:
#
#   * An AUTHORIZING row (PASS / CONDITIONAL PASS / HALT-RETRACTED) must be authenticated.
#     Forging one grants privilege.
#   * A RESTRICTIVE row (HALT) is honoured whether or not it authenticates. Forging one
#     costs availability, never privilege, and requiring a signature to *stop* an action
#     would mean an unsigned log fails open — the wrong direction.

#: Verdict that retracts an earlier HALT. C-2 says a HALT is final; it does not say a HALT is
#: eternal. What it forbids is a HALT lapsing *silently*, which is exactly what a later PASS
#: row did. A retraction is a separate, named, authenticated act that leaves a record.
_HALT_RETRACTION_VERDICT = "HALT-RETRACTED"

#: Ordered fields a decision-row HMAC signs, pipe-joined. Mirrors _WAIVER_SIGNATURE_FIELDS
#: (CH-05: one signing pattern, not two). `author` is included so a row is bound to who
#: issued it; `prev_digest` is included so rows chain and a deletion is detectable.
_DECISION_SIGNATURE_FIELDS: tuple[str, ...] = (
    "date",
    "action_reviewed",
    "verdict",
    "conditions_verified",
    "author",
    "prev_digest",
)

#: Author values that may issue an AUTHORIZING decision, absent a project roster. The security
#: sentinel is the only agent the constitution empowers to clear an action (Constitutional Rule
#: 1); the orchestrator routes to it, it does not stand in for it.
_DEFAULT_DECISION_AUTHORS: frozenset[str] = frozenset({"security", "@security"})

#: Optional project roster, one author per line, `#` comments allowed.
_DECISION_AUTHORS_FILE = "security-approvers.txt"


def _approved_decision_authors(output_dir: Path) -> frozenset[str]:
    """Return the author values permitted to issue an authorizing decision.

    Args:
        output_dir: The generated team's output directory.

    Returns:
        The roster from ``references/security-approvers.txt`` when that file exists,
        otherwise :data:`_DEFAULT_DECISION_AUTHORS`. A present-but-empty roster returns the
        default rather than an empty set — an empty file is far more likely to be an accident
        than a deliberate decision to make every action unclearable.
    """
    roster_path = output_dir / "references" / _DECISION_AUTHORS_FILE
    if not roster_path.exists():
        return _DEFAULT_DECISION_AUTHORS
    try:
        raw = roster_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"unable to read security approver roster: {exc}") from exc
    names = {
        line.strip().lower()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    return frozenset(names) or _DEFAULT_DECISION_AUTHORS


def _decision_author(row: dict[str, str]) -> str:
    """Return a decision row's author across both supported log schemas.

    The legacy six-column schema names it ``requesting_agent``; the current nine-column
    schema names it ``owner``. Both are checked so author binding works on either.
    """
    for field in ("requesting_agent", "owner", "author"):
        value = (row.get(field) or "").strip()
        if value:
            return value.lower().lstrip("@").strip() or value.lower()
    return ""


def _decision_signing_active(fieldnames: list[str]) -> bool:
    """True when decision rows must carry a verified signature.

    Two independent triggers, either of which turns enforcement on:

    * the log header carries a ``signature`` column — so a project opts in by adding the
      column, and cannot then opt out by leaving rows unsigned;
    * ``AGENTTEAMS_DECISION_SIGNING_KEY`` is set — so an operator can require signing even
      before the log has been migrated.

    Once active it is fail-closed: an unsigned or badly signed authorizing row is rejected,
    never downgraded to "unsigned but acceptable".
    """
    return "signature" in {c.strip() for c in fieldnames} or bool(
        os.getenv("AGENTTEAMS_DECISION_SIGNING_KEY", "")
    )


def _decision_signature_payload(row: dict[str, str]) -> str:
    """Return the pipe-joined payload a decision row's HMAC covers."""
    values = []
    for field in _DECISION_SIGNATURE_FIELDS:
        if field == "author":
            values.append(_decision_author(row))
        elif field == "action_reviewed":
            values.append((row.get("action_reviewed") or row.get("decision") or "").strip())
        elif field == "verdict":
            values.append((row.get("verdict") or row.get("status") or "").strip())
        elif field == "date":
            values.append((row.get("date") or row.get("timestamp") or "").strip())
        else:
            values.append((row.get(field) or "").strip())
    return "|".join(values)


def sign_decision_row(row: dict[str, str], *, key: str | None = None) -> str:
    """Return the HMAC-SHA256 signature for a decision row.

    Public because minting a signed row is an operator task, and a documented helper is
    better than a procedure the docs describe and nothing pins.

    Args:
        row: The decision row, using either supported schema.
        key: Signing key. Defaults to ``AGENTTEAMS_DECISION_SIGNING_KEY``.

    Returns:
        Hex digest.

    Raises:
        RuntimeError: When no signing key is available.
    """
    signing_key = key if key is not None else os.getenv("AGENTTEAMS_DECISION_SIGNING_KEY", "")
    if not signing_key:
        raise RuntimeError("decision signing key is not configured")
    return hmac.new(
        signing_key.encode("utf-8"),
        _decision_signature_payload(row).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


#: Where the strict agent-privilege switch is emitted (mirrors artifacts.AGENT_PRIVILEGE_REL_PATH).
_AGENT_PRIVILEGE_CONFIG = "agent-privilege.json"


def _enforce_decision_signing(output_dir: Path) -> bool:
    """True when this workspace requires authorizing decision rows to be signed.

    Reads the switch emitted at generate/update (``references/agent-privilege.json``). An
    ABSENT file is treated as OFF — a workspace that predates this feature keeps the legacy
    behavior (an unsigned authorizing row honoured on its author name) until it is updated,
    which is when the switch turns ON by default. A PRESENT-but-unreadable file fails CLOSED
    (raises): a security switch that cannot be read must not silently disable itself.

    Args:
        output_dir: The team root.

    Returns:
        Whether strict enforcement is active.

    Raises:
        RuntimeError: The config file exists but cannot be parsed.
    """
    config_path = output_dir / "references" / _AGENT_PRIVILEGE_CONFIG
    if not config_path.exists():
        return False
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"agent-privilege switch ({config_path}) is present but unreadable: {exc}. "
            "Refusing to silently disable strict enforcement — fix or remove the file."
        ) from exc
    return bool(payload.get("enforce_decision_signing"))


def _assert_authorizing_row_is_authentic(
    row: dict[str, str], *, output_dir: Path, signing_active: bool, action: str
) -> None:
    """Raise RuntimeError unless *row* may authorize *action*.

    Applied ONLY to authorizing verdicts. See the asymmetry note at the top of this section:
    a restrictive verdict is honoured unauthenticated on purpose.
    """
    if not _scope_permits(row, action):
        raise RuntimeError(
            f"decision authorizing '{action}' declares scope {row.get('scope')!r}, which does "
            f"not include '{action}'"
        )

    author = _decision_author(row)
    approved = _approved_decision_authors(output_dir)
    if author not in {a.lower().lstrip("@") for a in approved}:
        raise RuntimeError(
            f"decision authorizing '{action}' was issued by {author or 'an unnamed author'!r}, "
            f"which is not an approved security author ({sorted(approved)}). A requesting "
            f"agent cannot clear its own action."
        )

    if not signing_active:
        # Strict agent-privilege switch (enforce_decision_signing): when ON, an unsigned
        # authorization is REFUSED rather than honoured on its author name alone — closing
        # the roster/author-spoof path in the signing-inactive configuration. When OFF (the
        # switch absent, i.e. a workspace that predates the feature, or explicitly false),
        # the legacy behavior stands: the author check above is the only gate.
        if _enforce_decision_signing(output_dir):
            raise RuntimeError(
                f"decision authorizing '{action}' is UNSIGNED, but strict agent-privilege "
                f"enforcement (enforce_decision_signing) is ON for this workspace: an unsigned "
                f"authorization is refused (fail-closed). Activate decision signing — add a "
                f"`signature` column to the decisions log or set AGENTTEAMS_DECISION_SIGNING_KEY "
                f"— or set enforce_decision_signing:false in the brief and re-run --update."
            )
        return

    expected = sign_decision_row(row)
    actual = (row.get("signature") or "").strip().lower()
    if not actual:
        raise RuntimeError(
            f"decision authorizing '{action}' carries no signature, and signing is active"
        )
    if not hmac.compare_digest(expected, actual):
        raise RuntimeError(
            f"decision authorizing '{action}' failed signature verification"
        )


def _assert_decision_chain_intact(rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    """Verify the decision log's hash chain when the log carries one.

    Signing a row proves who wrote it; chaining proves nobody removed one. Without the chain,
    the cheapest attack on a signed log is deletion rather than forgery — a HALT row simply
    disappears and every remaining signature still verifies.

    A log with no ``prev_digest`` column is unchained and passes: chaining is opt-in the same
    way signing is, so an existing log keeps working until it is migrated.
    """
    if "prev_digest" not in {c.strip() for c in fieldnames}:
        return
    previous = ""
    for index, row in enumerate(rows):
        declared = (row.get("prev_digest") or "").strip()
        if declared != previous:
            raise RuntimeError(
                f"security decision log chain broken at row {index + 1}: prev_digest is "
                f"{declared!r}, expected {previous!r}. A row has been removed, reordered, or "
                f"edited."
            )
        previous = hashlib.sha256(
            _decision_signature_payload(row).encode("utf-8")
        ).hexdigest()


def _read_decision_rows(output_dir: Path) -> tuple[list[dict[str, str]], list[str]] | None:
    """Return ``(rows, fieldnames)`` for the decisions log, or None when it does not exist."""
    log_path = output_dir / "references" / "security-decisions.log.csv"
    if not log_path.exists():
        return None
    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = [c.strip() for c in (reader.fieldnames or [])]
            _security_decision_schema_kind(fieldnames)
            rows = [{k: (v or "") for k, v in row.items()} for row in reader]
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to read security decisions log: {exc}") from exc
    return rows, fieldnames


def _assert_no_unretracted_halt(output_dir: Path, *, action: str) -> None:
    """Raise RuntimeError when a HALT for *action* has never been explicitly retracted.

    This is C-2's "HALT is final" made mechanical. It scans **every** row rather than the
    latest one, because the latest-row rule — correct for choosing which clearance applies —
    let a later PASS silently supersede a HALT (probe A1), and let the `consumed` flag erase
    one (probe A3).

    A HALT is cleared only by a later :data:`_HALT_RETRACTION_VERDICT` row for the same
    action, carrying evidence, authenticated like any other authorizing row. `consumed` is
    ignored here entirely: anti-replay and retraction are different operations and sharing a
    flag between them is what made A3 work.
    """
    read = _read_decision_rows(output_dir)
    if read is None:
        return
    rows, fieldnames = read
    if not rows:
        return
    _assert_decision_chain_intact(rows, fieldnames)
    signing_active = _decision_signing_active(fieldnames)

    open_halts: list[tuple[int, dict[str, str]]] = []
    for index, row in enumerate(rows):
        reviewed = (row.get("action_reviewed") or row.get("decision") or "").strip()
        if not _action_matches(reviewed, action):
            continue
        verdict = (row.get("verdict") or row.get("status") or "").strip().upper()
        if verdict == "HALT":
            open_halts.append((index, row))
        elif verdict == _HALT_RETRACTION_VERDICT:
            _assert_authorizing_row_is_authentic(
                row, output_dir=output_dir, signing_active=signing_active, action=action
            )
            if not (row.get("evidence") or row.get("conditions") or "").strip():
                raise RuntimeError(
                    f"{_HALT_RETRACTION_VERDICT} row for '{action}' carries no evidence; a "
                    f"retraction must record why the HALT no longer applies"
                )
            open_halts.clear()

    if open_halts:
        index, row = open_halts[0]
        reviewed = (row.get("action_reviewed") or row.get("decision") or "").strip()
        raise RuntimeError(
            f"latest decision for action '{reviewed or action}' is HALT "
            f"(row {index + 1}, dated {(row.get('date') or row.get('timestamp') or '?').strip()}) "
            f"and has never been retracted. C-2: a HALT is cleared only by a "
            f"{_HALT_RETRACTION_VERDICT} row recording why it no longer applies — not by a "
            f"later PASS, and not by the `consumed` flag."
        )
