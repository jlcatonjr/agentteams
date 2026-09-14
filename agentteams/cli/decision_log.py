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

from agentteams.cli.effect_classifier import (
    EffectClassifierError,
    non_eligibility_reason,
    requires_operator_signature,
)
from agentteams.cli.signed_ledger import (
    SIG_SCHEME_ED25519,
    path_within,
    verify_by_scheme,
)


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

#: Optional column naming the action id(s) a decision derives from (semicolon/comma separated).
#: Workstream A: a HALT propagates along this lineage so renaming the scope-id of a HALTed action
#: — Gap A — no longer evades the C-2 scan. Opt-in like ``signature``/``prev_digest``: a log
#: without this column keeps the exact pre-lineage behaviour.
#:
#: It is a SIGNED axis (WS-A re-review F-1): appended to the signed payload **only when non-empty**
#: (the grants ``issuer_root`` seam), so a row minted without lineage keeps a byte-identical
#: payload — the ~100 legacy signatures still verify — while a row that carries lineage binds it
#: into both the HMAC and the hash chain. Without this, a keyless writer could add/clear/rewrite a
#: ``derives_from`` cell without breaking any signature, defeating the whole mechanism.
_DERIVES_FROM_FIELD = "derives_from"

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
    """Return the pipe-joined payload a decision row's HMAC (and hash chain) covers."""
    return "|".join(_decision_signature_values(row))


#: Optional signed axes appended (LABELLED) after the fixed fields, each only when non-empty. Labels
#: (``derives_from=…`` etc.) make the appends unambiguous — without them a row with
#: ``derives_from=x`` and one with ``sig_scheme=x`` would share a payload (WS-A re-review N-1). Each
#: is bound into both the HMAC and the hash chain, so a keyless writer cannot add, clear, relabel,
#: or rewrite any of them without breaking verification. ``sig_scheme``/``key_id`` MUST be signed
#: (WS-B): otherwise an attacker could relabel a relaxing row's scheme ed25519→hmac and forge a
#: cheap HMAC signature.
_OPTIONAL_SIGNED_AXES: tuple[str, ...] = (_DERIVES_FROM_FIELD, "sig_scheme", "key_id")


def _decision_signature_values(row: dict[str, str]) -> list[str]:
    """Return the ordered field VALUES a decision row's signature/chain covers.

    The fixed :data:`_DECISION_SIGNATURE_FIELDS` in order, then each non-empty optional signed axis
    as a ``name=value`` token. A row that carries none of the optional axes yields the exact
    pre-WS-A value list, so the ~100 legacy signatures verify byte-for-byte.
    """
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
    for axis in _OPTIONAL_SIGNED_AXES:
        value = (row.get(axis) or "").strip()
        if value:
            values.append(f"{axis}={value}")
    return values


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

#: The full column set an Ed25519-signed relaxing decision row needs. Used by the operator
#: ``--sign-decision`` verb to create or validate the log schema so a signed row's ``signature`` /
#: ``sig_scheme`` / ``key_id`` / ``derives_from`` / ``effect_*`` cells are actually persisted (a
#: narrower header would silently drop them and void the signature).
SIGNED_DECISION_COLUMNS: tuple[str, ...] = (
    "date", "action_reviewed", "verdict", "scope", "conditions", "conditions_verified",
    "evidence", "author", "derives_from", "effect_class", "effect_grants", "effect_targets",
    "effect_relaxes", "effect_destructive", "effect_cross_repo", "effect_bulk",
    "sig_scheme", "key_id", "prev_digest", "signature",
)


def append_signed_decision_row(output_dir: Path, row: dict[str, str]) -> None:
    """Append a fully-formed signed decision ``row`` to the security-decisions log (fail-closed).

    Creates the log with :data:`SIGNED_DECISION_COLUMNS` when absent. When a log already exists, its
    header MUST contain every signing column, else this refuses rather than silently dropping the
    ``signature``/``sig_scheme``/``key_id``/``derives_from``/``effect_*`` cells (which would void the
    signature). The row is written with the existing header's columns preserved.

    Args:
        output_dir: The team root.
        row: The fully-populated, already-signed row (caller computes prev_digest + signature).

    Raises:
        RuntimeError: The existing log header lacks a signing column (migration needed).
    """
    import csv as _csv

    from agentteams.atomicio import _atomic_write_text, atomic_rewrite_csv_rows

    log_path = output_dir / "references" / "security-decisions.log.csv"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    required = {"signature", "sig_scheme", "key_id", "derives_from"}
    if log_path.exists():
        read = _read_decision_rows(output_dir)
        rows, fieldnames = (read if read is not None else ([], []))
        missing = required - {c.strip() for c in fieldnames}
        if missing:
            raise RuntimeError(
                f"security-decisions log at {log_path} lacks signing column(s) {sorted(missing)}; "
                f"refusing to append a signed row that would be silently truncated. Migrate the log "
                f"header to include {sorted(required)} first (fail-closed)."
            )
        columns = list(fieldnames)
        rows = rows + [row]
        atomic_rewrite_csv_rows(log_path, rows, columns)
    else:
        columns = list(SIGNED_DECISION_COLUMNS)
        _atomic_write_text(log_path, ",".join(columns) + "\n")
        with log_path.open("a", encoding="utf-8", newline="") as fh:
            _csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore").writerow(
                {c: row.get(c, "") for c in columns}
            )


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
        # GV1 fail-closed: a governed workspace asserts (via a git-tracked signing-governed.marker) that
        # signing MUST be enforced. If the config is absent there, RAISE rather than silently reverting
        # to legacy OFF — renaming references/ must not disable strict enforcement.
        if (output_dir / "signing-governed.marker").exists():
            raise RuntimeError(
                f"governed workspace ({output_dir}) but {config_path} is absent — refusing to fall "
                "back to legacy unsigned behaviour (fail-closed)."
            )
        return False
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"agent-privilege switch ({config_path}) is present but unreadable: {exc}. "
            "Refusing to silently disable strict enforcement — fix or remove the file."
        ) from exc
    return bool(payload.get("enforce_decision_signing"))


def _assert_relaxing_row_is_traceable(
    row: dict[str, str], *, output_dir: Path, action: str
) -> None:
    """Refuse a constraint-relaxing EXCEPTION row that declares no resolvable lineage.

    Workstream A, fail-closed. An exception authorization must be traceable — it must say what it
    derives from — so it cannot dodge an ancestor's HALT by appearing as a fresh, unrelated action
    (Gap A). Three deliberate scopings keep this from breaking normal workflows:

    * it fires **only in a governed workspace** (``_enforce_decision_signing`` — the same switch
      that turns on strict signing); an ungoverned/legacy log is unaffected;
    * it applies **only to the narrow exception subset** (``requires_operator_signature`` — capability
      grants, governance-root writes, explicit relaxations, cross-repo), NOT to routine destructive
      actions (``overwrite``/``prune``), which keep the ordinary ``@security`` C-5 clearance without a
      forced lineage. (Residual, documented: a routine destructive action renamed around a specific
      HALT is not caught by *this* requirement — but the effect classifier still classifies it
      destructive, so it still needs @security clearance; only the exception subset is forced to be
      traceable.);
    * a self-declared ``effect_class`` that dangerously diverges from the derived class already
      raised inside the classifier; that surfaces here as a fail-closed refusal.

    Args:
        row: The authorizing row.
        output_dir: The team root (governed-workspace check).
        action: The action being authorized (for the message).

    Raises:
        RuntimeError: The row is an exception (or its effect declaration is inconsistent) and it
            declares no resolvable ``derives_from`` lineage, in a governed workspace.
    """
    if not _enforce_decision_signing(output_dir):
        return
    # A HALT-RETRACTED row is relaxing by definition, but its target is already identified by the
    # action-id it matches (and it is separately authenticated + evidence-checked), so it does not
    # additionally need a derives_from cell (WS-A re-review F-5 — avoids bricking a governed
    # workspace's retraction rows).
    verdict = (row.get("verdict") or row.get("status") or "").strip().upper()
    if verdict == _HALT_RETRACTION_VERDICT:
        return
    try:
        needs_lineage = requires_operator_signature(row, kind="decision")
    except EffectClassifierError as exc:
        raise RuntimeError(
            f"decision authorizing '{action}' has an inconsistent effect declaration: {exc}"
        ) from exc
    if not needs_lineage:
        return
    lineage = _row_lineage(row)
    if not lineage:
        raise RuntimeError(
            f"decision authorizing '{action}' has a relaxing effect but declares no "
            f"'{_DERIVES_FROM_FIELD}' lineage. In a governed workspace a relaxing authorization "
            f"must be traceable so it cannot evade an ancestor HALT by renaming (Gap A) — record "
            f"the recorded decision action-id(s) it derives from (fail-closed)."
        )
    # Reconcile with _transitive_ancestors (WS-A re-review F-3, v2): run the SAME whole-chain
    # traversal the HALT scan runs, not a one-level parent check — so traceability rejects exactly
    # what the transitive scan rejects (a deep-dangling reference or a cycle through recorded ids),
    # rather than accepting a row the end-to-end gate then fails closed on. The row under review may
    # not be in the log yet, so overlay its own lineage onto the map before traversing.
    read = _read_decision_rows(output_dir)
    lineage_map = _build_lineage_map(read[0] if read else [])
    this_action = _row_action_id(row) or (action or "").strip().lower()
    lineage_map.setdefault(this_action, set()).update(lineage)
    try:
        _transitive_ancestors(this_action, lineage_map)
    except RuntimeError as exc:
        raise RuntimeError(
            f"decision authorizing '{action}' has an untraceable relaxing lineage: {exc}. A "
            f"relaxing authorization's {_DERIVES_FROM_FIELD} must name recorded decision "
            f"action-id(s) with no dangling reference or cycle (fail-closed)."
        ) from exc


#: Where the tracked Ed25519 public verify keys live, one per key-id (WS-B). Agents hold only these
#: public keys (they can verify a relaxing authorization, never mint one). This dir is itself a
#: trust anchor (WS-D/R1): it is in the governance-target vocabulary and must be integrity-pinned +
#: sandbox denyWrite so an agent cannot drop its own public key and self-sign.
_VERIFY_KEY_STORE_REL = "references/authorized-verify-keys"

#: A key-id must be a safe bare filename stem (no path separators / traversal).
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _load_verify_key(output_dir: Path, key_id: str) -> str:
    """Return the PEM of the Ed25519 public verify key named by ``key_id`` (fail-closed).

    Args:
        output_dir: The team root.
        key_id: The signed ``key_id`` selecting which public key to verify against.

    Returns:
        The PEM text.

    Raises:
        RuntimeError: ``key_id`` is malformed, the key file is missing (the relaxing path is then
            *unavailable* — the availability gate), or it cannot be read / escapes the store.
    """
    if not key_id or not _KEY_ID_RE.match(key_id):
        raise RuntimeError(f"relaxing authorization names a missing/invalid key_id ({key_id!r})")
    store = output_dir / _VERIFY_KEY_STORE_REL
    path = store / f"{key_id}.pub.pem"
    if not path_within(str(path), str(store), base=output_dir):
        raise RuntimeError(f"verify-key path for key_id {key_id!r} escapes the key store")
    if not path.exists():
        raise RuntimeError(
            f"no Ed25519 verify key for key_id {key_id!r} at {_VERIFY_KEY_STORE_REL}/"
            f"{key_id}.pub.pem — the relaxing authorization path is unavailable (fail-closed)."
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"unable to read verify key for key_id {key_id!r}: {exc}") from exc


def _assert_ed25519_sufficient(row: dict[str, str], *, output_dir: Path, action: str) -> None:
    """Refuse a relaxing authorizing row unless it is validly **Ed25519**-signed (validity≠sufficiency).

    The WS-B chokepoint. A relaxing authorization requires the asymmetric scheme: a valid HMAC
    signature on a relaxing row is *valid-but-insufficient* and is refused — that split is what
    stops a key-holder (or compromised runner) downgrading a relaxing grant to the cheap symmetric
    scheme. ``sig_scheme`` and ``key_id`` are signed fields, so an attacker cannot relabel the
    scheme without breaking verification.

    Args:
        row: The authorizing row (already classified relaxing by the caller).
        output_dir: The team root (verify-key store lookup).
        action: The action being authorized (for the message).

    Raises:
        RuntimeError: The scheme is not ed25519, the key_id/verify-key is missing, or verification
            fails or is unavailable (backend absent) — all fail-closed.
    """
    scheme = (row.get("sig_scheme") or "").strip().lower()
    if scheme != SIG_SCHEME_ED25519:
        raise RuntimeError(
            f"decision authorizing relaxing action '{action}' declares sig_scheme "
            f"{scheme or 'hmac'!r}, but a relaxing authorization REQUIRES ed25519: a valid HMAC "
            f"signature is valid-but-INSUFFICIENT for a relaxing row (no downgrade). Only the "
            f"operator's Ed25519 signature can authorize this."
        )
    public_pem = _load_verify_key(output_dir, (row.get("key_id") or "").strip())
    signature = (row.get("signature") or "").strip()
    try:
        ok = verify_by_scheme(
            SIG_SCHEME_ED25519,
            _decision_signature_values(row),
            signature,
            public_pem=public_pem,
        )
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(
            f"decision authorizing relaxing action '{action}' could not be Ed25519-verified: "
            f"{exc} (fail-closed)."
        ) from exc
    if not ok:
        raise RuntimeError(
            f"decision authorizing relaxing action '{action}' failed Ed25519 verification"
        )


def _assert_authorizing_row_is_authentic(
    row: dict[str, str], *, output_dir: Path, signing_active: bool, action: str
) -> None:
    """Raise RuntimeError unless *row* may authorize *action*.

    Applied ONLY to authorizing verdicts. See the asymmetry note at the top of this section:
    a restrictive verdict is honoured unauthenticated on purpose.

    In a **governed** workspace a **relaxing** row takes the elevated path — lineage traceability
    (WS-A) + Ed25519 sufficiency (WS-B) — and does NOT fall through to the symmetric HMAC check (a
    relaxing row is ed25519-signed, not HMAC-signed). Non-relaxing rows, and any row in an
    ungoverned/legacy workspace, keep the existing author + HMAC path unchanged.
    """
    if not _scope_permits(row, action):
        raise RuntimeError(
            f"decision authorizing '{action}' declares scope {row.get('scope')!r}, which does "
            f"not include '{action}'"
        )

    # Workstream E: categorical non-eligibility, checked BEFORE any signature so a valid signature
    # cannot buy an ineligible effect. Some effects (writing a governance/trust root, a sweeping
    # cross-repo+destructive+bulk grant) are never authorizable via a per-row exception — not even
    # with the operator's Ed25519 signature; they have their own privileged governance process.
    reason = non_eligibility_reason(row, kind="decision")
    if reason is not None:
        raise RuntimeError(
            f"decision authorizing '{action}' is categorically NON-ELIGIBLE: it {reason}. Such a "
            f"change is never authorizable via a security-decision exception (even signed) — it "
            f"requires the privileged governance process (fail-closed)."
        )

    author = _decision_author(row)
    approved = _approved_decision_authors(output_dir)
    if author not in {a.lower().lstrip("@") for a in approved}:
        raise RuntimeError(
            f"decision authorizing '{action}' was issued by {author or 'an unnamed author'!r}, "
            f"which is not an approved security author ({sorted(approved)}). A requesting "
            f"agent cannot clear its own action."
        )

    # Classify once (fail-closed on an inconsistent self-declaration). The ELEVATED (operator
    # Ed25519) path is gated on the NARROW exception subset (requires_operator_signature), not every
    # relaxing/destructive action — a routine destructive action keeps the @security C-5 HMAC path
    # below. A HALT-RETRACTED verdict is excluded here (its retraction handling is unchanged; it
    # still needs an approved author + signature + evidence via the normal path).
    verdict = (row.get("verdict") or row.get("status") or "").strip().upper()
    try:
        needs_operator_sig = requires_operator_signature(row, kind="decision")
    except EffectClassifierError as exc:
        raise RuntimeError(
            f"decision authorizing '{action}' has an inconsistent effect declaration: {exc}"
        ) from exc

    if (
        needs_operator_sig
        and verdict != _HALT_RETRACTION_VERDICT
        and _enforce_decision_signing(output_dir)
    ):
        # Elevated path (governed + constraint-relaxing exception): traceable lineage AND operator
        # Ed25519 signature (validity != sufficiency — a valid HMAC here is refused).
        _assert_relaxing_row_is_traceable(row, output_dir=output_dir, action=action)
        _assert_ed25519_sufficient(row, output_dir=output_dir, action=action)
        return

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

    # WS-A, final gate: an authenticated, signed authorization must still be traceable if its
    # effect is relaxing (governed workspaces only — see the helper). Placed last so the existing
    # scope/author/signing refusals keep their ordering and messages.
    _assert_relaxing_row_is_traceable(row, output_dir=output_dir, action=action)


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


def _row_action_id(row: dict[str, str]) -> str:
    """Return a row's own action id, normalized (lowercased, stripped)."""
    return (row.get("action_reviewed") or row.get("decision") or "").strip().lower()


def _row_lineage(row: dict[str, str]) -> set[str]:
    """Return the normalized parent action ids a row declares in ``derives_from`` (may be empty)."""
    raw = row.get(_DERIVES_FROM_FIELD, "") or ""
    return {part.strip().lower() for part in re.split(r"[;,]", raw) if part.strip()}


def _build_lineage_map(rows: list[dict[str, str]]) -> dict[str, set[str]]:
    """Map each row action id to the union of parent ids it declares across all its rows."""
    lineage: dict[str, set[str]] = {}
    for row in rows:
        action_id = _row_action_id(row)
        if not action_id:
            continue
        lineage.setdefault(action_id, set()).update(_row_lineage(row))
    return lineage


def _transitive_ancestors(action: str, lineage_map: dict[str, set[str]]) -> set[str]:
    """Return the transitive ``derives_from`` ancestors of *action*, FAIL-CLOSED.

    Raises rather than returning a partial set on any lineage defect, because a relaxing action
    that evades its ancestor's HALT by a broken or fabricated lineage must refuse, not proceed:

    * **nonexistent-id** — a ``derives_from`` naming an id that no row in the log defines (not a
      key in ``lineage_map``): an untraceable / fabricated ancestor;
    * **cycle** — a ``derives_from`` chain that revisits a node on the current path.

    Args:
        action: The requested action id (normalized inside).
        lineage_map: Output of :func:`_build_lineage_map`.

    Returns:
        The set of transitive ancestor ids (excluding *action* itself).

    Raises:
        RuntimeError: A dangling reference or a cycle — traversal fails closed.
    """
    start = action.strip().lower()
    ancestors: set[str] = set()
    on_path: set[str] = set()   # nodes on the current recursion stack (cycle = edge into this)
    explored: set[str] = set()  # nodes fully descended (safe to skip RE-descending, not the edge)

    def _descend(node: str) -> None:
        on_path.add(node)
        for parent in lineage_map.get(node, set()):
            if parent in on_path:
                # A back-edge to a node on the current stack is a genuine cycle — regardless of
                # which node it was first discovered from (WS-A re-review F-4: the earlier global
                # dedup let an off-first-path cycle escape).
                raise RuntimeError(
                    f"lineage cycle detected at {parent!r} (via {node!r})"
                )
            if parent not in lineage_map:
                raise RuntimeError(
                    f"lineage reference {parent!r} (from {node!r}) names no recorded decision "
                    f"row — a relaxing action cannot derive from a nonexistent/untraceable id"
                )
            ancestors.add(parent)
            if parent not in explored:
                _descend(parent)
        on_path.discard(node)
        explored.add(node)

    _descend(start)
    return ancestors


def _open_halt_for(
    rows: list[dict[str, str]], target: str, *, output_dir: Path, signing_active: bool
) -> tuple[int, dict[str, str]] | None:
    """Return the first unretracted HALT for exactly *target*, or None.

    Encapsulates the whole-log scan-and-clear for one action id (the pre-lineage behaviour),
    so the lineage-aware caller can run it for the requested action AND each ancestor without
    duplicating the retraction authentication or evidence requirement.
    """
    open_halts: list[tuple[int, dict[str, str]]] = []
    for index, row in enumerate(rows):
        reviewed = (row.get("action_reviewed") or row.get("decision") or "").strip()
        if not _action_matches(reviewed, target):
            continue
        verdict = (row.get("verdict") or row.get("status") or "").strip().upper()
        if verdict == "HALT":
            open_halts.append((index, row))
        elif verdict == _HALT_RETRACTION_VERDICT:
            _assert_authorizing_row_is_authentic(
                row, output_dir=output_dir, signing_active=signing_active, action=target
            )
            if not (row.get("evidence") or row.get("conditions") or "").strip():
                raise RuntimeError(
                    f"{_HALT_RETRACTION_VERDICT} row for '{target}' carries no evidence; a "
                    f"retraction must record why the HALT no longer applies"
                )
            open_halts.clear()
    return open_halts[0] if open_halts else None


def _assert_no_unretracted_halt(output_dir: Path, *, action: str) -> None:
    """Raise RuntimeError when a HALT for *action* (or a lineage ancestor) is unretracted.

    This is C-2's "HALT is final" made mechanical. It scans **every** row rather than the
    latest one, because the latest-row rule — correct for choosing which clearance applies —
    let a later PASS silently supersede a HALT (probe A1), and let the `consumed` flag erase
    one (probe A3).

    A HALT is cleared only by a later :data:`_HALT_RETRACTION_VERDICT` row for the same
    action, carrying evidence, authenticated like any other authorizing row. `consumed` is
    ignored here entirely: anti-replay and retraction are different operations and sharing a
    flag between them is what made A3 work.

    **Workstream A — lineage (Gap A, secondary mechanism).** When the log carries a
    :data:`_DERIVES_FROM_FIELD` column, the scan also follows the requested action's transitive
    ``derives_from`` ancestors: a HALT on any ancestor blocks the derived action too. Traversal is
    FAIL-CLOSED — a cycle or a reference to an id no row defines raises rather than silently
    resolving to "no HALT" — and the lineage cell is now inside the signature+chain envelope
    (:func:`_decision_signature_payload`), so it cannot be tampered keyless.

    **Honest scope (WS-A re-review F-2/F-6).** Lineage is *self-declared* — it is C-4 data the row's
    author writes. This scan therefore only propagates a HALT along *honestly declared* lineage; it
    does NOT by itself stop a hostile rename that simply declares a benign (or no) ancestor. The
    real defence against a rename is the **effect classifier** (which derives the relaxing class
    from effect, not name) forcing the elevated asymmetric-signed path (WS-B), backed by
    :func:`_assert_relaxing_row_is_traceable` requiring a relaxing row to at least name a resolvable
    ancestor. "Gap A closed" is the conjunction of those three — not this scan alone. A log without
    the column keeps the exact pre-lineage behaviour (opt-in).
    """
    read = _read_decision_rows(output_dir)
    if read is None:
        return
    rows, fieldnames = read
    if not rows:
        return
    _assert_decision_chain_intact(rows, fieldnames)
    signing_active = _decision_signing_active(fieldnames)

    targets: list[tuple[str, bool]] = [(action, False)]  # (id, is_ancestor)
    if _DERIVES_FROM_FIELD in {c.strip() for c in fieldnames}:
        try:
            ancestors = _transitive_ancestors(action, _build_lineage_map(rows))
        except RuntimeError as exc:
            raise RuntimeError(
                f"lineage traversal for action '{action}' failed closed: {exc}"
            ) from exc
        targets.extend((ancestor, True) for ancestor in sorted(ancestors))

    for target, is_ancestor in targets:
        offending = _open_halt_for(
            rows, target, output_dir=output_dir, signing_active=signing_active
        )
        if offending is None:
            continue
        index, row = offending
        reviewed = (row.get("action_reviewed") or row.get("decision") or "").strip()
        via = "" if not is_ancestor else (
            f" '{action}' derives from '{target}', which is under an unretracted HALT — "
        )
        raise RuntimeError(
            f"{via}latest decision for action '{reviewed or target}' is HALT "
            f"(row {index + 1}, dated {(row.get('date') or row.get('timestamp') or '?').strip()}) "
            f"and has never been retracted. C-2: a HALT is cleared only by a "
            f"{_HALT_RETRACTION_VERDICT} row recording why it no longer applies — not by a "
            f"later PASS, not by the `consumed` flag, and not by renaming the action."
        )
