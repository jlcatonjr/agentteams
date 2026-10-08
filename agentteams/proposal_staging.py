"""proposal_staging.py — staged and direct writes for MCP-mediated agent writes (R3).

Under ``write_policy: "orchestrator-only"`` an agent granted the ``agentteams_runner`` MCP server submits its writes
through the runner. By default they are **staged**: the runner runs every check and gate, counts a nonce use, and
stores the artifact (without its nonce) under :data:`STAGED_REL`. The orchestrator then approves (:func:`apply_staged`,
which re-runs every check against the file as it is now, with identity taken from the record) or rejects
(:func:`reject_staged`). An agent with a verified signed direct-write grant may instead write **directly**
(:func:`apply_direct`); a deletion is never direct (C-5).

The validate-gate-act body is :func:`agentteams.proposals._apply`, shared by every mode, so the checks can't diverge.
Records live in the runner-only control plane (sessions under the switch can't write ``.agentteams/``, and since R1
their built-in read tools can't open it either). Carved out of ``proposals.py`` to stay under the CH-07 ceiling;
integrity-pinned.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams import proposals as P
from agentteams.atomicio import read_regular_nofollow, write_new_atomic
from agentteams.proposal_policy import Policy, ProposalError

#: Where staged proposals wait for the orchestrator.
STAGED_REL = ".agentteams/staged"
#: How long a staged proposal stays approvable.
STAGED_TTL_HOURS = 24
#: Largest staged record read back.
STAGED_MAX_BYTES = 4 * 1024 * 1024
_SID_RE = re.compile(r"^[0-9a-f]{32}$")


def stage(root: Path, artifact: dict[str, Any], agent: str, rel: str, *, gates: list[Any], size: int,
          new_hash: str | None) -> dict[str, Any]:
    """Store a validated artifact, without its nonce, ledger it, and return the agent's receipt.

    Called by :func:`agentteams.proposals._apply` in ``stage`` mode, after every check and gate passed and a use
    was counted.

    Args:
        root: The project root.
        artifact: The validated artifact.
        agent: The resolved agent.
        rel: The validated project-relative path.
        gates: Gate results.
        size: Content bytes (0 for a deletion).
        new_hash: The content's sha256 (None for a deletion).

    Returns:
        The receipt: ``{agent, path, sid, kind, base_sha256, new_sha256, bytes, gates, expires, written: False,
        staged: True}``.

    Raises:
        OSError: The record can't be written.
    """
    sid = secrets.token_hex(16)
    stored = {k: v for k, v in artifact.items() if k not in ("dispatch", "agent")}
    expires = (datetime.now(UTC) + timedelta(hours=STAGED_TTL_HOURS)).isoformat(timespec="seconds")
    record = {"sid": sid, "agent": agent, "path": rel, "kind": artifact["kind"], "artifact": stored, "gates": gates,
              "bytes": size, "content_sha256": new_hash, "expires": expires}
    # Signed with the ledger key, which only the runner holds: approval refuses a record swapped or edited in between
    # (any same-user process can write .agentteams/, so its permissions alone don't protect it; R3 review).
    record["mac"] = P._mac(P._key(), record)
    directory = root / STAGED_REL
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    P.record(root, {"action": "stage-proposal", "agent": agent, "path": rel, "sid": sid, "kind": artifact["kind"],
                    "base": artifact["base_sha256"], "new": new_hash, "bytes": size, "gates": gates,
                    "rationale": str(artifact.get("rationale", "")).strip()[:300]})
    write_new_atomic(directory, f"{sid}.json", json.dumps(record).encode("utf-8"))
    return {"agent": agent, "path": rel, "sid": sid, "kind": artifact["kind"], "base_sha256": artifact["base_sha256"],
            "new_sha256": new_hash, "bytes": size, "gates": gates, "expires": expires, "written": False,
            "staged": True}


def stage_proposal(artifact: dict[str, Any], *, root: Path, policy: Policy, confine: bool = False,
                   expect_agent: str | None = None) -> dict[str, Any]:
    """Validate and gate a change or deletion proposal, count a use, and store it for the orchestrator.

    Args:
        artifact: A parsed ``change-proposal`` or ``delete-proposal`` carrying the agent's dispatch nonce.
        root: The project root.
        policy: The team's policy.
        confine: Run gates in the OS sandbox (the runner does).
        expect_agent: The MCP channel's server-instance agent (R2).

    Returns:
        The receipt from :func:`stage`.

    Raises:
        ProposalError: Any check or gate refuses (logged); nothing is stored.
    """
    return P._apply(artifact, root=root, policy=policy, confine=confine, expect_agent=expect_agent, mode="stage")


def apply_direct(artifact: dict[str, Any], *, root: Path, policy: Policy, grant: dict[str, Any] | None,
                 used: int = 0, confine: bool = False, expect_agent: str | None = None) -> dict[str, Any]:
    """Apply an agent's change proposal at once, for an agent with a verified direct-write grant.

    Args:
        artifact: A parsed ``change-proposal``. A ``delete-proposal`` is always refused: deletes are staged (C-5).
        root: The project root.
        policy: The team's policy; the agent must be in ``policy.direct_agents``.
        confine: Run gates in the OS sandbox (the runner does).
        expect_agent: The MCP channel's server-instance agent (R2).
        grant: The agent's verified grant (R5); required. Its expiry and write cap are checked first, and its id
            goes into the ledger row.
        used: Direct writes already made under the grant (the runner's count from a verified ledger).

    Returns:
        As :func:`agentteams.proposals.apply_proposal`.

    Raises:
        ProposalError: A deletion, an agent without a verified grant, an expired or spent grant, or any check or
            gate refusing (logged).
    """
    if not grant:
        raise ProposalError("a direct write needs a verified grant record; stage the proposal instead")
    from agentteams.mcp_direct_grants import check_write_allowed

    check_write_allowed(grant, used)
    return P._apply(artifact, root=root, policy=policy, confine=confine, expect_agent=expect_agent, mode="direct",
                    ledger_extra={"grant": grant["grant_id"]})


def _load(root: Path, sid: Any) -> tuple[Path, dict[str, Any]]:
    if not isinstance(sid, str) or not _SID_RE.match(sid):
        raise ProposalError("bad staged id")
    path = root / STAGED_REL / f"{sid}.json"
    try:
        record = json.loads(read_regular_nofollow(path, STAGED_MAX_BYTES))
    except (OSError, ValueError) as exc:
        raise ProposalError(f"no staged proposal {sid}") from exc
    if not isinstance(record, dict) or record.get("sid") != sid or not isinstance(record.get("artifact"), dict):
        raise ProposalError(f"staged record {sid} is malformed")
    return path, record


def _verified(sid: str, record: dict[str, Any]) -> dict[str, Any]:
    """Refuse a record whose signature, binding or content hash doesn't hold (R3 review, conditions 1 and 3)."""
    body = {k: v for k, v in record.items() if k != "mac"}
    mac = record.get("mac")
    if not (isinstance(mac, str) and P.hmac.compare_digest(mac, P._mac(P._key(), body))):
        raise ProposalError(f"staged record {sid} is not signed by this runner's key; refused (reject it)")
    artifact = record["artifact"]
    if artifact.get("path") != record.get("path") or artifact.get("kind") != record.get("kind"):
        raise ProposalError(f"staged record {sid} doesn't match its own binding; refused")
    content = artifact.get("content")
    actual = hashlib.sha256(content.encode("utf-8")).hexdigest() if isinstance(content, str) else None
    if actual != record.get("content_sha256"):
        raise ProposalError(f"staged record {sid}: content doesn't match its sha256; refused")
    try:
        expires = datetime.fromisoformat(str(record.get("expires")))
    except (TypeError, ValueError) as exc:
        raise ProposalError(f"staged record {sid} has a malformed expiry") from exc
    if expires < datetime.now(UTC):
        raise ProposalError(f"staged proposal {sid} expired; reject it and have the agent re-stage")
    return record


def apply_staged(sid: str, *, root: Path, policy: Policy, confine: bool = False) -> dict[str, Any]:
    """The orchestrator's approval: apply a staged proposal as its agent, re-running every check.

    Args:
        sid: The staged id from the agent's receipt.
        root: The project root.
        policy: The team's policy.
        confine: Run gates in the OS sandbox (the runner does).

    Returns:
        As :func:`agentteams.proposals.apply_proposal`, plus ``sid``. The record is removed once applied.

    Raises:
        ProposalError: An unknown or expired id, or a re-run check or gate refusing (logged; the record stays, so
            the orchestrator can reject it or the agent can re-stage against the current file).
    """
    path, record = _load(root, sid)
    record = _verified(sid, record)
    result = P._apply(dict(record["artifact"]), root=root, policy=policy, confine=confine, mode="staged",
                      as_agent=str(record["agent"]))
    path.unlink(missing_ok=True)
    return {**result, "sid": sid}


def reject_staged(sid: str, *, root: Path, reason: str = "") -> dict[str, Any]:
    """Drop a staged proposal and ledger the rejection.

    Args:
        sid: The staged id.
        root: The project root.
        reason: Why (recorded, capped at 300 characters).

    Returns:
        ``{sid, agent, path, rejected: True}``.

    Raises:
        ProposalError: An unknown id.
    """
    path, record = _load(root, sid)
    P.record(root, {"action": "reject-staged", "agent": record.get("agent"), "path": record.get("path"), "sid": sid,
                    "reason": str(reason).strip()[:300]})
    path.unlink(missing_ok=True)
    return {"sid": sid, "agent": record.get("agent"), "path": record.get("path"), "rejected": True}


def list_staged(root: Path) -> list[dict[str, Any]]:
    """Every staged proposal's metadata, without content, oldest first.

    Args:
        root: The project root.

    Returns:
        ``[{sid, agent, kind, path, bytes, content_sha256, gates, expires}]``.

    Raises:
        Nothing: malformed records are listed by id with ``malformed: True``; expired ones are purged (ledgered).
    """
    directory = root / STAGED_REL
    if not directory.is_dir():
        return []
    out: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            _p, record = _load(root, path.stem)
        except ProposalError:
            out.append({"sid": path.stem, "malformed": True})
            continue
        try:
            expired = datetime.fromisoformat(str(record.get("expires"))) < now
        except (TypeError, ValueError):
            expired = False  # listed (and refused on approval); the orchestrator rejects it
        if expired:  # purge: an expired record can never be applied
            P.record(root, {"action": "expire-staged", "agent": record.get("agent"), "path": record.get("path"),
                            "sid": record.get("sid")})
            path.unlink(missing_ok=True)
            continue
        out.append({k: record.get(k) for k in ("sid", "agent", "kind", "path", "bytes", "content_sha256", "gates",
                                               "expires")})
    return out


def show_staged(sid: str, *, root: Path) -> dict[str, Any]:
    """One staged proposal in full, content included, for the orchestrator to read on cause.

    Args:
        sid: The staged id.
        root: The project root.

    Returns:
        The stored record. It never holds the nonce.

    Raises:
        ProposalError: An unknown id.
    """
    return _load(root, sid)[1]


__all__ = ["STAGED_REL", "STAGED_TTL_HOURS", "apply_direct", "apply_staged", "list_staged", "reject_staged",
           "show_staged", "stage", "stage_proposal"]
