"""Agent escalations: fail-safe, persistent stops that only the operator can clear.

When the remediation agent hits a stop condition (suspected malicious code, an indicator of
compromise, a prompt injection, an integrity failure, or something else it cannot judge) it
*escalates*. The out-of-sandbox executor records the escalation here, in the MAC-chained action
ledger (:mod:`agentteams.vulnscan.ledger`), and from then on refuses remediation for the
escalation's **scope** until the operator resolves it.

- **Scope is decided here, never by the caller.** ``malicious``, ``ioc``, ``integrity`` and the
  internal ``escalation-flood`` block every target (``"*"``). ``injection`` and ``other`` block the
  canonical target (a repository top-level path or an exact host name), or everything when the
  target does not canonicalise.
- **Rows.** An escalation is a ledger row of kind ``escalation`` with fields ``esc_id``,
  ``esc_kind``, ``scope``, ``evidence``, ``request_id`` and ``run_id``. A resolution is a row of
  kind ``resolved`` with ``esc_id``, ``esc_kind``, ``scope``, ``evidence`` and ``method``
  (``"operator-mac"`` for ``other``; ``"operator-ed25519"`` plus ``issued``, ``key_id`` and
  ``signature`` for serious kinds).
- **Flood guard.** More than :data:`MAX_PER_RUN` escalations in one run records a single ``"*"``
  ``escalation-flood`` for that run instead, so an agent cannot bury the review in rows.
- **Serious kinds** (:data:`SERIOUS`) clear only with an **operator Ed25519 signature** over a
  purpose-tagged resolution record, verified against the operator verify-key store (the same
  ownership and mode checks as standing grants). Kind ``other`` clears with an operator
  resolution recorded in the MAC'd ledger (:func:`resolve_other`).
- A ledger integrity error (``ValueError`` from :func:`ledger.read`) **propagates** from every
  function here. Callers must treat it as "blocked".

Locking: :func:`ledger.locked` does not nest. :func:`record` must be called *inside* it (the
executor holds it for the whole request). :func:`resolve_other` and
:func:`apply_signed_resolution` take it themselves. :func:`open_escalations` and
:func:`blocking` read without it.
"""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agentteams.vulnscan import ledger

KINDS: tuple[str, ...] = ("malicious", "ioc", "injection", "integrity", "other")
AGENT_KINDS: tuple[str, ...] = KINDS            # "escalation-flood" is internal only
FLOOD_KIND = "escalation-flood"
SERIOUS: frozenset[str] = frozenset({"malicious", "ioc", "injection", "integrity", FLOOD_KIND})
GLOBAL_KINDS: frozenset[str] = frozenset({"malicious", "ioc", "integrity", FLOOD_KIND})
MAX_PER_RUN = 10
EVIDENCE_MAX = 2000
RESOLUTION_PURPOSE = "agentteams-vulnscan-escalation-resolution-v1"
RESOLUTION_FIELDS = ("esc_id", "evidence", "issued", "key_id")
ALL_SCOPE = "*"
ROW_ESCALATION = "escalation"
ROW_RESOLVED = "resolved"

_ID_RE = re.compile(r"^ve-[0-9a-f]{16}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])")
_FUTURE_SKEW = timedelta(minutes=5)


class EscalationError(ValueError):
    """An escalation could not be recorded or resolved."""


@dataclass(frozen=True)
class Escalation:
    """One recorded escalation.

    Attributes:
        id: ``ve-<16 hex>``.
        kind: One of :data:`KINDS` or ``escalation-flood``.
        scope: ``"*"`` (every target) or a canonical target.
        evidence: Sanitised evidence text (untrusted agent content).
        request_id: The request that raised it.
        opened_ts: The ledger row's timestamp.
        run_id: The run that raised it.
    """

    id: str
    kind: str
    scope: str
    evidence: str
    request_id: str
    opened_ts: str
    run_id: str


# ---------------------------------------------------------------- helpers

def sanitize_evidence(text: str, limit: int = EVIDENCE_MAX) -> str:
    """Strip ANSI escapes and control/format characters, keeping newlines and tabs; cap the length.

    Args:
        text: Raw evidence (untrusted).
        limit: Maximum characters kept.

    Returns:
        The cleaned text, at most ``limit`` characters.
    """
    text = _ANSI_RE.sub("", str(text))
    kept = (ch for ch in text
            if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co"))
    return "".join(kept)[:limit]


def _one_line(text: str) -> str:
    text = _ANSI_RE.sub("", str(text))
    out = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp") else ch
                  for ch in text)
    return " ".join(out.split())


def _from_row(row: dict[str, Any]) -> Escalation:
    return Escalation(id=str(row["esc_id"]), kind=str(row["esc_kind"]), scope=str(row["scope"]),
                      evidence=str(row.get("evidence", "")), request_id=str(row.get("request_id", "")),
                      opened_ts=str(row.get("ts", "")), run_id=str(row.get("run_id", "")))


def _rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return rows if rows is not None else ledger.read()


def scope_for(kind: str, target: str, canonical: Callable[[str], str | None]) -> str:
    """Return the scope an escalation of ``kind`` on ``target`` blocks.

    Args:
        kind: Escalation kind (:data:`KINDS` or ``escalation-flood``).
        target: The target the agent named (untrusted).
        canonical: Canonicaliser, normally ``executor.canonical_target``.

    Returns:
        ``"*"`` for global kinds or a target that does not canonicalise; else the canonical target.

    Raises:
        EscalationError: Unknown kind.
    """
    if kind not in KINDS and kind != FLOOD_KIND:
        raise EscalationError(f"unknown escalation kind {kind!r}")
    if kind in GLOBAL_KINDS:
        return ALL_SCOPE
    canon = canonical(str(target)) if target else None
    return canon if canon else ALL_SCOPE


def open_escalations(rows: list[dict[str, Any]] | None = None) -> list[Escalation]:
    """Return escalations with no later ``resolved`` row, in ledger order.

    Args:
        rows: Pre-read ledger rows (optional; read and verified when omitted).

    Returns:
        The open escalations.

    Raises:
        ValueError: The ledger fails verification (callers treat this as blocked).
    """
    opened: dict[str, Escalation] = {}
    for row in _rows(rows):
        kind = row.get("kind")
        if kind == ROW_ESCALATION and row.get("esc_id"):
            opened[str(row["esc_id"])] = _from_row(row)
        elif kind == ROW_RESOLVED:
            opened.pop(str(row.get("esc_id", "")), None)
    return list(opened.values())


def blocking(target: str, rows: list[dict[str, Any]] | None = None) -> Escalation | None:
    """Return an open escalation that blocks ``target``, preferring a ``"*"`` one.

    Pass the canonical target (``executor.canonical_target``) so that a subdirectory of an
    escalated repository matches its scope.

    Args:
        target: Canonical target (repository top-level path or host name).
        rows: Pre-read ledger rows (optional).

    Returns:
        The blocking escalation, or None.

    Raises:
        ValueError: The ledger fails verification (callers treat this as blocked).
    """
    found: Escalation | None = None
    for esc in open_escalations(rows):
        if esc.scope == ALL_SCOPE:
            return esc
        if found is None and target and esc.scope == target:
            found = esc
    return found


# ---------------------------------------------------------------- recording

def record(kind: str, target: str, evidence: str, *, request_id: str, run_id: str,
           canonical: Callable[[str], str | None]) -> Escalation:
    """Record an agent escalation. Caller locks: call inside :func:`ledger.locked`.

    A duplicate of an open (scope, kind) returns the existing escalation and writes nothing. Once
    a run has :data:`MAX_PER_RUN` escalations, further ones record a single ``"*"``
    ``escalation-flood`` for the run, returned again while it is open. (Only after the operator
    resolves it does a further escalation in that run record a fresh flood: fail safe.)

    Args:
        kind: One of :data:`AGENT_KINDS` (``escalation-flood`` is refused as input).
        target: The target the agent named (untrusted).
        evidence: The agent's evidence (untrusted; sanitised and capped here).
        request_id: The originating request id.
        run_id: The current run id.
        canonical: Canonicaliser, normally ``executor.canonical_target``.

    Returns:
        The recorded (or existing) escalation.

    Raises:
        EscalationError: ``kind`` is not an agent kind, or ``run_id`` is empty.
        ValueError: The ledger fails verification.
    """
    if kind not in AGENT_KINDS:
        raise EscalationError(f"escalation kind must be one of {AGENT_KINDS}")
    if not run_id:
        raise EscalationError("an escalation needs a run_id")
    scope = scope_for(kind, target, canonical)
    rows = ledger.read()
    current = open_escalations(rows)
    for esc in current:
        if esc.scope == scope and esc.kind == kind:
            return esc
    in_run = [r for r in rows if r.get("kind") == ROW_ESCALATION and r.get("run_id") == run_id]
    if len(in_run) >= MAX_PER_RUN:
        # An open flood (this run's or an earlier one) already blocks everything. When the operator
        # has resolved this run's flood and the agent keeps escalating, record a fresh one (fail safe).
        for esc in current:
            if esc.kind == FLOOD_KIND:
                return esc
        text = (f"more than {MAX_PER_RUN} escalations in run {run_id}; the latest was {kind!r}: "
                + sanitize_evidence(evidence))
        return _append(FLOOD_KIND, ALL_SCOPE, text, request_id=request_id, run_id=run_id)
    return _append(kind, scope, evidence, request_id=request_id, run_id=run_id)


def record_flood(detail: str, *, run_id: str, request_id: str = "") -> tuple[Escalation, bool]:
    """Open (or return the open) ``escalation-flood``, which blocks every target. Caller locks.

    Used by the executor when the spool holds more requests than it serves in one pass.

    Args:
        detail: Executor-written description (counts only).
        run_id: The current run id.
        request_id: Originating request id, if any.

    Returns:
        ``(escalation, newly_opened)``.

    Raises:
        ValueError: The ledger fails verification.
    """
    for esc in open_escalations():
        if esc.kind == FLOOD_KIND:
            return esc, False
    return _append(FLOOD_KIND, ALL_SCOPE, detail, request_id=request_id, run_id=run_id), True


def _append(kind: str, scope: str, evidence: str, *, request_id: str, run_id: str) -> Escalation:
    row = ledger.append(ROW_ESCALATION, esc_id="ve-" + secrets.token_hex(8), esc_kind=kind, scope=scope,
                        evidence=sanitize_evidence(evidence), request_id=str(request_id), run_id=str(run_id))
    return _from_row(row)


# ---------------------------------------------------------------- resolution

def _find_open(esc_id: str, rows: list[dict[str, Any]]) -> Escalation:
    for esc in open_escalations(rows):
        if esc.id == esc_id:
            return esc
    raise EscalationError(f"{esc_id!r} is not an open escalation (unknown or already resolved)")


def resolve_other(esc_id: str, evidence: str) -> dict[str, Any]:
    """Resolve an open escalation of kind ``other`` with an operator note. Takes the ledger lock.

    Args:
        esc_id: Escalation id.
        evidence: The operator's reason (required, non-empty after sanitising).

    Returns:
        The written ``resolved`` row.

    Raises:
        EscalationError: Empty evidence, the escalation is not open, or it is serious.
        ValueError: The ledger fails verification.
    """
    text = sanitize_evidence(evidence).strip()
    if not text:
        raise EscalationError("a resolution needs non-empty evidence")
    with ledger.locked():
        esc = _find_open(str(esc_id), ledger.read())
        if esc.kind != "other":
            raise EscalationError(f"{esc.id} is {esc.kind!r}: serious escalations need a signed resolution")
        return ledger.append(ROW_RESOLVED, esc_id=esc.id, esc_kind=esc.kind, scope=esc.scope, evidence=text,
                             method="operator-mac")


def resolution_signed_values(rec: dict[str, Any]) -> list[str]:
    """Return the exact Ed25519 payload: purpose tag + canonical JSON of every bound field.

    Args:
        rec: Resolution record.

    Returns:
        Field list for ``signed_ledger.ed25519_sign``/``ed25519_verify``.

    Raises:
        KeyError: A bound field is missing.
    """
    body = {name: rec[name] for name in RESOLUTION_FIELDS}
    return [RESOLUTION_PURPOSE, json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)]


def build_resolution(esc_id: str, evidence: str, key_id: str, *,
                     now: datetime | None = None) -> dict[str, Any]:
    """Build an unsigned resolution record for an open serious escalation.

    Args:
        esc_id: Escalation id.
        evidence: The operator's reason (required, non-empty after sanitising).
        key_id: Operator verify-key id.
        now: Clock override (tests).

    Returns:
        The unsigned record (fields :data:`RESOLUTION_FIELDS`).

    Raises:
        EscalationError: Malformed id or key id, empty evidence, the escalation is not open, or it
            is not serious (use :func:`resolve_other`).
        ValueError: The ledger fails verification.
    """
    if not _ID_RE.match(str(esc_id)):
        raise EscalationError(f"malformed escalation id {esc_id!r}")
    if not _KEY_ID_RE.match(str(key_id)):
        raise EscalationError(f"malformed key_id {key_id!r}")
    text = sanitize_evidence(evidence).strip()
    if not text:
        raise EscalationError("a resolution needs non-empty evidence")
    esc = _find_open(esc_id, ledger.read())
    if esc.kind not in SERIOUS:
        raise EscalationError(f"{esc_id} is {esc.kind!r}: use resolve_other")
    now = now or datetime.now(UTC)
    return {"esc_id": esc_id, "evidence": text, "issued": now.replace(microsecond=0).isoformat(), "key_id": key_id}


def sign_resolution(record: dict[str, Any], *, confirm_sha256: str | None = None,
                    allow_checkout: bool = False) -> dict[str, Any] | None:
    """Sign a resolution through :func:`grant.operator_sign` (the gate is the operator's private key file).

    Args:
        record: Unsigned record from :func:`build_resolution`.
        confirm_sha256: Exact review digest, for non-terminal confirmation.
        allow_checkout: ``--allow-checkout-signing`` (as for other operator signatures).

    Returns:
        The signed record, or None after printing why nothing was signed.
    """
    from agentteams.vulnscan import grant

    return grant.operator_sign(record, digest_kind="vulnscan-escalation-resolution", fields=RESOLUTION_FIELDS,
                               values=resolution_signed_values,
                               banner="About to sign the RESOLUTION of a serious vulnerability-remediation "
                                      "escalation. Once applied,\nremediation resumes for its scope. Check the "
                                      "evidence in the review first:",
                               confirm_sha256=confirm_sha256, allow_checkout=allow_checkout)


def _verify_key(key_id: str) -> str:
    from agentteams.vulnscan import grant

    try:
        return grant.verify_key(key_id)
    except grant.GrantError as exc:
        raise EscalationError(str(exc)) from exc


def verify_resolution(record: dict[str, Any], *, now: datetime | None = None) -> None:
    """Verify a signed resolution's shape and Ed25519 signature (not the ledger state).

    Args:
        record: Signed resolution.
        now: Clock override (tests).

    Raises:
        EscalationError: Anything is wrong (fail closed).
    """
    from agentteams.cli.signed_ledger import ed25519_verify

    if not isinstance(record, dict) or set(record) != {*RESOLUTION_FIELDS, "signature"}:
        raise EscalationError("unexpected or missing fields in the resolution")
    if not _ID_RE.match(str(record["esc_id"])) or not _KEY_ID_RE.match(str(record["key_id"])):
        raise EscalationError("malformed esc_id or key_id")
    if not isinstance(record["evidence"], str) or not record["evidence"].strip():
        raise EscalationError("a resolution needs non-empty evidence")
    try:
        issued = datetime.fromisoformat(str(record["issued"]))
    except ValueError as exc:
        raise EscalationError(f"malformed issued timestamp: {exc}") from exc
    if issued.tzinfo is None or issued > (now or datetime.now(UTC)) + _FUTURE_SKEW:
        raise EscalationError("issued must be a timezone-aware time that is not in the future")
    try:
        ok = ed25519_verify(_verify_key(str(record["key_id"])), resolution_signed_values(record),
                            str(record["signature"]))
    except RuntimeError as exc:
        raise EscalationError(f"cannot verify: {exc}") from exc
    if not ok:
        raise EscalationError("signature does not verify")


def apply_signed_resolution(record: dict[str, Any]) -> dict[str, Any]:
    """Verify a signed resolution, then record it. Takes the ledger lock.

    Args:
        record: Signed record from :func:`sign_resolution`.

    Returns:
        The written ``resolved`` row (it includes the signature).

    Raises:
        EscalationError: Bad signature or shape, the escalation is not open, or it is not serious.
        ValueError: The ledger fails verification.
    """
    verify_resolution(record)
    with ledger.locked():
        esc = _find_open(str(record["esc_id"]), ledger.read())
        if esc.kind not in SERIOUS:
            raise EscalationError(f"{esc.id} is {esc.kind!r}: use resolve_other")
        return ledger.append(ROW_RESOLVED, esc_id=esc.id, esc_kind=esc.kind, scope=esc.scope,
                             evidence=sanitize_evidence(record["evidence"]), method="operator-ed25519",
                             issued=str(record["issued"]), key_id=str(record["key_id"]),
                             signature=str(record["signature"]))


def describe(esc: Escalation) -> str:
    """Return a one-line description, control characters stripped.

    Args:
        esc: The escalation.

    Returns:
        ``"<id> [<kind>] scope=<scope> run=<run> opened=<ts>: <evidence, truncated>"``.
    """
    ev = _one_line(esc.evidence)
    if len(ev) > 160:
        ev = ev[:157] + "..."
    return _one_line(f"{esc.id} [{esc.kind}] scope={esc.scope} run={esc.run_id} opened={esc.opened_ts}: "
                     f"{ev or '(no evidence)'}")
