"""mcp_direct_grants.py — operator-signed direct-write grants for MCP-mediated agent writes (R5).

By default an agent's writes through the ``agentteams_runner`` server are **staged** for the orchestrator. A grant of
``approval: direct`` in the brief's ``mcp_grants`` lets the agent's writes land at once, which removes the
orchestrator's look at each write: a constraint-relaxing change (Rule 15). So the runner honours ``direct`` only when an
**operator Ed25519-signed grant record** verifies, and refuses everyone else.

Where the records live: an operator-owned file **outside the project**, ``~/.config/agentteams/mcp-grants/
<basename>-<hash>.json``, under the same custody checks as the ``confined_programs`` file (P5b). No session can write
there, so a revoked grant (removed from the file) can't be put back by an agent or the orchestrator. The operator's
public keys live beside it, in ``~/.config/agentteams/verify-keys/<key_id>.pub.pem``.

What a record binds (all signed, with its own purpose tag :data:`PURPOSE_TAG`, so it can't be replayed as any other
signed artifact): grant id, agent, server, exact tools, approval, the agent's write scopes, the team's gate names, the
installed server's sha256, issue and expiry times, and a cap on direct writes. At start the runner verifies every record
against the live brief (any drift refuses that grant), enforces the aggregate cap :data:`MAX_ACTIVE`, pins the file's
hash (a change needs a restart), and ledgers which grants are active. On every direct write it re-checks expiry and the
write cap. Plan: ``references/plans/mcp-mediated-agent-writes.plan.md`` R5 (@security C1, C2, C6).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams import confinement as _confinement
from agentteams.frameworks._sandbox_emit import MCP_GRANTS_DIR as _MCP_GRANTS_DIR
from agentteams.frameworks._sandbox_emit import OPERATOR_VERIFY_KEYS_DIR as _OPERATOR_VERIFY_KEYS_DIR
from agentteams.proposal_policy import Policy, ProposalError

#: Domain separation: a direct-grant signature can never verify as any other signed artifact.
PURPOSE_TAG = "agentteams-mcp-direct-grant-v1"
GRANTS_DIR = _MCP_GRANTS_DIR
VERIFY_KEYS_DIR = _OPERATOR_VERIFY_KEYS_DIR
#: Aggregate cap: more active direct grants than this refuses them all (relaxations can't silently proliferate).
MAX_ACTIVE = 3
#: Sunset: a grant may run at most this long from issue.
MAX_DAYS = 30
#: A grant's write cap may not exceed this.
MAX_WRITES = 500
#: Dispatch limits for agents holding a verified direct grant (@security C6): shorter-lived, fewer uses.
DIRECT_TTL_HOURS = 4
DIRECT_MAX_USES = 10
MAX_FILE_BYTES = 256 * 1024
_FIELDS = ("grant_id", "agent", "server", "map_version", "tools", "approval", "write_scopes", "gates", "server_sha256",
           "issued",
           "expires", "max_writes", "key_id")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def grants_file_for(root: Path) -> Path:
    """The operator-owned grants file for *root*: ``~/.config/agentteams/mcp-grants/<basename>-<hash>.json``.

    Args:
        root: The project root.

    Returns:
        The expanded path (named like the ``confined_programs`` file, so two checkouts never share one).

    Raises:
        Nothing.
    """
    return Path(os.path.expanduser(GRANTS_DIR)) / _confinement.confined_file_for(root).name


def signed_values(record: dict[str, Any]) -> list[str]:
    """The exact Ed25519 payload: :data:`PURPOSE_TAG`, then every bound field in fixed order.

    Args:
        record: A grant record.

    Returns:
        The field list ``signed_ledger`` canonicalises and signs.

    Raises:
        KeyError: A bound field is missing.
    """
    # One canonical JSON field (sorted keys, lists kept as arrays, no whitespace): no ',' / '|' / trimming
    # ambiguity between distinct records (R5 review condition 1).
    body = {name: record[name] for name in _FIELDS}
    return [PURPOSE_TAG, json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)]


def expected_binding(agent: str, policy: Policy, brief: dict[str, Any]) -> dict[str, Any]:
    """What a grant for *agent* must bind, from the live brief and policy (any drift refuses the grant).

    Args:
        agent: The agent.
        policy: The loaded policy.
        brief: The parsed brief.

    Returns:
        ``{server, tools, approval, write_scopes, gates, server_sha256}``.

    Raises:
        ProposalError: The brief doesn't grant *agent* ``approval: direct`` with any tools.
    """
    from agentteams import runner_mcp

    grant = (brief.get("mcp_grants") or {}).get(agent) or {}
    if grant.get("approval") != "direct":
        raise ProposalError(f"the brief's mcp_grants.{agent} doesn't ask for approval 'direct'")
    tools = [t for t in runner_mcp.TOOLS if t in (grant.get("tools") or [])]
    scopes = sorted(str(s) for s in (policy.agent_policies.get(agent) or {}).get("write_scopes") or [])
    # Each gate bound by a hash of its whole definition, not just its name: a gate edited to always pass
    # breaks the grant (R5 review condition 1).
    gates = {name: hashlib.sha256(json.dumps(policy.gates[name], sort_keys=True).encode()).hexdigest()
             for name in sorted(policy.gates)}
    return {"server": runner_mcp.SERVER_NAME, "tools": tools, "approval": "direct", "write_scopes": scopes,
            "gates": gates, "server_sha256": runner_mcp.SHA256, "map_version": runner_mcp.MAP_VERSION}


def _require_bounded(binding: dict[str, Any]) -> None:
    """A direct grant needs a gate, or write scopes that are files rather than directories (plan §3)."""
    if not binding["write_scopes"]:
        raise ProposalError("a direct grant needs the agent to have write scopes")
    if not binding["gates"] and any(s.endswith("/") or "*" in s for s in binding["write_scopes"]):
        raise ProposalError("a direct grant needs at least one gate, or write scopes that name single files")


def _public_key(key_id: str) -> str:
    if not _KEY_ID_RE.match(key_id or ""):
        raise ProposalError(f"malformed key_id {key_id!r}")
    directory = Path(os.path.expanduser(VERIFY_KEYS_DIR))
    path = directory / f"{key_id}.pub.pem"
    try:
        _confinement._check_custody(directory, directory=True)
        _confinement._check_custody(path, directory=False)
    except (OSError, _confinement.ConfinementError) as exc:
        raise ProposalError(f"no usable operator verify key {path} ({exc})") from exc
    return path.read_text(encoding="utf-8")


def read_grants(root: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Read the operator-owned grants file (custody-checked), if there is one.

    Args:
        root: The project root.

    Returns:
        ``(records, sha256 of the file)``, or ``([], None)`` when there is no file.

    Raises:
        ProposalError: The file or its directory fails custody (a link, not yours, writable by others, inside the
            project), is too large, or isn't a JSON list of objects.
    """
    path = grants_file_for(root)
    if not os.path.lexists(path):
        return [], None
    try:
        _confinement._check_custody(path.parent, directory=True)
        _confinement._check_custody(path, directory=False)
    except (OSError, _confinement.ConfinementError) as exc:
        raise ProposalError(f"{path}: {exc}") from exc
    if _confinement._inside(path, root):
        raise ProposalError(f"{path} lies inside the project; agents could write it")
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ProposalError(f"{path} is over {MAX_FILE_BYTES} bytes")
    try:
        records = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ProposalError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ProposalError(f"{path} must hold a JSON list of grant records")
    return records, hashlib.sha256(raw).hexdigest()


def verify(record: dict[str, Any], policy: Policy, brief: dict[str, Any], *,
           now: datetime | None = None) -> dict[str, Any]:
    """Verify one grant record: signature, binding to the live brief, sunset and caps.

    Args:
        record: The grant record.
        policy: The loaded policy.
        brief: The parsed brief.
        now: The time to check against (tests).

    Returns:
        The record, verified.

    Raises:
        ProposalError: Any field missing, a bad signature, a binding that drifted from the brief, an expired or
            over-long grant, or an out-of-range write cap.
    """
    from agentteams.cli.signed_ledger import SIG_SCHEME_ED25519, verify_by_scheme

    now = now or datetime.now(UTC)
    missing = [f for f in (*_FIELDS, "signature") if f not in record]
    if missing:
        raise ProposalError(f"grant record missing {', '.join(missing)}")
    agent = str(record["agent"])
    try:
        ok = verify_by_scheme(SIG_SCHEME_ED25519, signed_values(record), str(record["signature"]),
                              public_pem=_public_key(str(record["key_id"])))
    except (RuntimeError, ValueError) as exc:
        raise ProposalError(f"grant {record.get('grant_id')!r} can't be verified: {exc}") from exc
    if not ok:
        raise ProposalError(f"grant {record.get('grant_id')!r}: the signature doesn't verify")
    binding = expected_binding(agent, policy, brief)
    for name, want in binding.items():
        if record[name] != want:
            raise ProposalError(f"grant {record['grant_id']!r}: {name} no longer matches the brief or the installed "
                                "server; re-sign it")
    _require_bounded(binding)
    try:
        issued, expires = datetime.fromisoformat(str(record["issued"])), datetime.fromisoformat(str(record["expires"]))
    except ValueError as exc:
        raise ProposalError(f"grant {record['grant_id']!r} has malformed times") from exc
    if expires <= now:
        raise ProposalError(f"grant {record['grant_id']!r} expired")
    if expires - issued > timedelta(days=MAX_DAYS) or issued > now + timedelta(minutes=5):
        raise ProposalError(f"grant {record['grant_id']!r} runs longer than {MAX_DAYS} days or starts in the future")
    if not (isinstance(record["max_writes"], int) and 1 <= record["max_writes"] <= MAX_WRITES):
        raise ProposalError(f"grant {record['grant_id']!r}: max_writes must be 1-{MAX_WRITES}")
    return record


def active_grants(root: Path, policy: Policy, brief: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str],
                                                                              str | None]:
    """Every verified grant, keyed by agent, with the problems found and the file's hash.

    Fails closed on the aggregate cap: more than :data:`MAX_ACTIVE` valid grants activates none.

    Args:
        root: The project root.
        policy: The loaded policy.
        brief: The parsed brief.

    Returns:
        ``(agent -> record, problems, file sha256 or None)``.

    Raises:
        ProposalError: The grants file itself is unusable (custody, size, shape).
    """
    records, sha = read_grants(root)
    grants: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for record in records:
        try:
            verified = verify(record, policy, brief)
        except ProposalError as exc:
            problems.append(str(exc))
            continue
        if verified["agent"] in grants:
            problems.append(f"more than one grant for {verified['agent']}; none of them is used")
            grants[verified["agent"]] = {}
            continue
        grants[verified["agent"]] = verified
    grants = {a: g for a, g in grants.items() if g}
    if len(grants) > MAX_ACTIVE:
        problems.append(f"{len(grants)} active direct grants exceed the cap of {MAX_ACTIVE}; none is used")
        grants = {}
    return grants, problems, sha


def count_direct_writes(root: Path) -> dict[str, int]:
    """Direct writes per grant id in the ledger, counted only after the whole ledger verifies (R5 condition 3).

    The runner calls this once at start and keeps the counts in memory, so deleting ledger rows or the ledger itself
    can't reset a grant's write cap: a broken chain refuses here, and a missing ledger starts the runner afresh only
    with the operator's restart.

    Args:
        root: The project root.

    Returns:
        ``grant id -> apply-direct rows``.

    Raises:
        ProposalError: The ledger doesn't verify (chain, head or signatures).
    """
    from agentteams import proposals as P

    problems = P.verify_ledger(root) if (root / P.LEDGER_REL).exists() else []
    if problems:
        raise ProposalError("the proposal ledger doesn't verify, so direct-write counts can't be trusted: "
                            + "; ".join(problems[:3]))
    counts: dict[str, int] = {}
    key = P._key()
    ledger = root / P.LEDGER_REL
    for line in (ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []):
        row = P._verified(key, line)
        if row and row.get("action") == "apply-direct" and row.get("grant"):
            counts[str(row["grant"])] = counts.get(str(row["grant"]), 0) + 1
    return counts


def check_write_allowed(grant: dict[str, Any], used: int, *, now: datetime | None = None) -> None:
    """Per write: the grant hasn't expired and its write cap isn't spent (@security C2).

    Args:
        grant: The verified grant.
        used: Direct writes already made under it (the runner's in-memory count).
        now: The time to check against (tests).

    Returns:
        None.

    Raises:
        ProposalError: The grant expired, or its ``max_writes`` are spent.
    """
    now = now or datetime.now(UTC)
    if datetime.fromisoformat(str(grant["expires"])) <= now:
        raise ProposalError(f"direct grant {grant['grant_id']!r} expired; stage the write instead")
    if used >= int(grant["max_writes"]):
        raise ProposalError(f"direct grant {grant['grant_id']!r} has used its {grant['max_writes']} writes; "
                            "stage further writes")


def key_fingerprint(key_id: str) -> str:
    """The sha256 of the operator verify key a grant names, for the runner's start-up ledger row (condition 2).

    Args:
        key_id: The key's id.

    Returns:
        A hex digest, or ``"unreadable"``.

    Raises:
        Nothing.
    """
    try:
        return hashlib.sha256(_public_key(key_id).encode()).hexdigest()
    except ProposalError:
        return "unreadable"


def sign_record(record: dict[str, Any], private_pem: str, *, password: bytes | None = None) -> dict[str, Any]:
    """Sign a grant record with the operator's Ed25519 key (run outside every agent session).

    Args:
        record: Every :data:`_FIELDS` value.
        private_pem: The operator's private key PEM.
        password: Its passphrase, if encrypted.

    Returns:
        The record with ``signature``.

    Raises:
        RuntimeError: The signing backend (``agentteams[signing]``) is absent or the key isn't Ed25519.
    """
    from agentteams.cli.signed_ledger import ed25519_sign

    return {**record, "signature": ed25519_sign(private_pem, signed_values(record), password=password)}


__all__ = ["DIRECT_MAX_USES", "DIRECT_TTL_HOURS", "GRANTS_DIR", "MAX_ACTIVE", "MAX_DAYS", "MAX_WRITES", "PURPOSE_TAG",
           "VERIFY_KEYS_DIR", "active_grants", "check_write_allowed", "count_direct_writes", "expected_binding",
           "grants_file_for", "key_fingerprint", "read_grants", "sign_record", "signed_values", "verify"]
