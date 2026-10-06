"""Orchestrator-only writes, P1: apply typed change proposals and run typed command requests.

Under the opt-in ``write_policy: "orchestrator-only"`` pilot (operator decisions 2026-10-06), a non-orchestrator
agent does not edit files or run commands. It returns one of three artifacts:

* a **change proposal**: ``{kind: "change-proposal", dispatch, path, base_sha256, content, rationale, gates?}``.
  The full new UTF-8 text, checked against the hash of the file the agent read;
* a **deletion proposal**: ``{kind: "delete-proposal", dispatch, path, base_sha256, rationale}``;
* a **command request**: ``{kind: "command-request", dispatch, argv, cwd?, purpose, expected_writes?,
  stdin_from_content?}``. An argument list, never a shell string.

**Identity comes from a dispatch nonce, not from the caller.** When the orchestrator dispatches an agent it calls
:func:`issue_dispatch` (``agentteams --issue-dispatch --agent SLUG``). That records an HMAC-signed
``{nonce, agent, expires}`` row in the control plane, and the nonce goes into the agent's task. Every artifact
must carry that nonce, and the agent is read from the signed record. A proposal's prose cannot steer the
orchestrator into acting as a different agent.

**Policy** (from the brief; validated by :func:`load_policy`):

* ``agent_policies[agent].write_scopes``: files, or directories ending in ``/``, the agent may change;
* ``agent_policies[agent].commands``: ``[{prefix, args, cwd?, variadic?, writes?, stdin_gates?}]``.
  - ``argv`` must start with ``prefix`` exactly, and each remaining argument must fullmatch its pattern.
  - ``variadic: true`` lets the last pattern match one or more trailing arguments.
  - Patterns that would admit an option (``-x``) or a parent path (``../x``) are refused at load, so ``.*``
    cannot silently reopen ``lake env bash -c ...``.
  - ``argv[0]`` is resolved on a sanitized PATH and must not live inside the project.
  - ``cwd`` is pinned by the entry.
  - ``writes`` lists the globs the command may change; ``expected_writes`` must fall inside it, or inside the
    agent's scopes.
  - ``stdin_gates`` names the gates run on ``stdin_from_content``. Without it, stdin content is refused.
* ``proposal_gates[name] = {glob, argv}``: pre-write checks on the *proposed* content. ``{file}`` (a temporary
  copy) and ``{path}`` may appear only as whole argv elements. Shell-interpreter gates are refused. Globs are
  matched case-insensitively.
* ``protected_paths``: globs no proposal may touch. A pattern without wildcards also covers everything below
  it.

**Always refused:**
- the project control plane (``_write_roots.control_plane_of``);
- the brief that defines this policy;
- anything resolving outside the project.

**Execution.** Gates and commands run with a scrubbed environment: an allowlist of locale and ``HOME``
variables plus a PATH of absolute entries, and never a signing key. Allowed commands that run agent-written
code (``lake build`` compiling a Lean file with compile-time IO, a test runner) are still arbitrary execution.
Contain them with the OS sandbox profiles (pilot P4), not with this allowlist. Dry runs execute gates.

**Undeclared writes fail the run.** Tracked, untracked and control-plane files are compared before and after.
Paths ignored by git (build directories) are not seen; that is the sandbox's job.

**Ledger.** Every action and every refusal appends an HMAC-signed, hash-chained row to
``.agentteams/proposal-ledger.jsonl``, under a file lock. ``.agentteams/proposal-ledger.head`` (also signed)
anchors the row count and last hash, so an edited, removed, reordered, truncated or recomputed ledger fails
:func:`verify_ledger`. The key is ``AGENTTEAMS_PROPOSAL_LEDGER_KEY`` (else ``AGENTTEAMS_DECISION_SIGNING_KEY``).
Without one, every operation is refused.

Stdlib only; integrity-pinned.
"""

from __future__ import annotations

import fcntl  # POSIX-only: the pilot's confinement (Seatbelt/bwrap) is POSIX-only too
import fnmatch
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams import confinement as _confinement
from agentteams.proposal_policy import (  # re-exported: the policy half of this module (CH-07 carve)
    DEFAULT_SIZE_CAP,  # noqa: F401  re-exported
    Policy,
    ProposalError,
    _in_scope,
    load_policy,  # noqa: F401  re-exported
)
from agentteams.atomicio import _atomic_write_text
from agentteams.frameworks._write_roots import control_plane_of

GATE_TIMEOUT = 120
COMMAND_TIMEOUT = 600
DISPATCH_TTL_HOURS = 24
#: Artifacts one dispatch may submit (proposals + requests) before the agent must be re-dispatched.
DISPATCH_MAX_USES = 25
LEDGER_REL = ".agentteams/proposal-ledger.jsonl"
HEAD_REL = ".agentteams/proposal-ledger.head"
DISPATCH_REL = ".agentteams/dispatches.jsonl"
#: A lock file that is never replaced, so issue/compaction and use counting serialize on one inode.
DISPATCH_LOCK_REL = ".agentteams/dispatches.lock"
KEY_ENV = ("AGENTTEAMS_PROPOSAL_LEDGER_KEY", "AGENTTEAMS_DECISION_SIGNING_KEY")
#: Key-file custody (P4a): the out-of-session runner reads the key from this file, never from its environment
#: (a sandboxed child can read a same-user parent's exec-time environment via KERN_PROCARGS2). The file must sit
#: in KEY_DIR, which every session sandbox read-denies.
KEY_FILE_ENV = "AGENTTEAMS_PROPOSAL_LEDGER_KEY_FILE"
KEY_DIR = "~/.config/agentteams/keys"
DEFAULT_KEY_FILE = KEY_DIR + "/proposal-ledger.key"

#: Environment variables passed to gates and commands; everything else (signing keys, tokens) is withheld.
SAFE_ENV = ("HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "USER", "LOGNAME", "TERM", "ELAN_HOME")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_KEYS = {
    "change-proposal": ({"kind", "dispatch", "path", "base_sha256", "content", "rationale"}, {"gates", "agent"}),
    "delete-proposal": ({"kind", "dispatch", "path", "base_sha256", "rationale"}, {"agent"}),
    "command-request": ({"kind", "dispatch", "argv", "purpose"},
                        {"cwd", "expected_writes", "stdin_from_content", "agent"}),
}


class LedgerTamperedError(ProposalError):
    """The ledger, its head or the dispatch records changed while a command ran; nothing more is recorded."""


class UndeclaredWritesError(ProposalError):
    """A command ran but changed files outside its declared writes; ``result`` holds its output."""

    def __init__(self, message: str, result: dict[str, Any]) -> None:
        super().__init__(message)
        self.result = result


def _sandbox_for(policy: Policy) -> str | None:
    """The usable sandbox, or None when the policy's logged opt-out allows running unconfined."""
    sandbox = _confinement.available()
    if sandbox is None and not policy.allow_unconfined:
        raise ProposalError("no usable OS sandbox here (sandbox-exec/bwrap missing, or this process is already "
                            "sandboxed); refusing to run unconfined. Start the runner outside every agent "
                            "session, or set allow_unconfined_runs in the brief (every run is then logged)")
    return sandbox


# --- keys, records, ledger ----------------------------------------------------------------------


#: Set by :func:`use_key_file`; when set, it is the only key source (environment keys are ignored).
_FILE_KEY: bytes | None = None


def use_key_file(path: str | None = None) -> Path:
    """Switch to key-file custody: read the ledger key from *path* and ignore every environment key.

    Args:
        path: The key file. Defaults to ``$AGENTTEAMS_PROPOSAL_LEDGER_KEY_FILE`` or :data:`DEFAULT_KEY_FILE`.

    Returns:
        The resolved key-file path.

    Raises:
        ProposalError: When the file is outside :data:`KEY_DIR`, missing, a symlink, not a regular file, not
            owned by this user, readable by group or others, or empty.
    """
    global _FILE_KEY
    target = Path(os.path.expanduser(path or os.environ.get(KEY_FILE_ENV) or DEFAULT_KEY_FILE))
    key_dir = Path(os.path.expanduser(KEY_DIR)).resolve()
    if not Path(os.path.abspath(target)).parent.resolve().is_relative_to(key_dir):
        raise ProposalError(f"ledger key file {target} must sit in {KEY_DIR}, which session sandboxes read-deny")
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ProposalError(f"ledger key file {target} cannot be opened (missing or a symlink): {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid():
            raise ProposalError(f"ledger key file {target} must be a regular file you own, with mode 0600")
        data = os.read(fd, 4096).strip()
    finally:
        os.close(fd)
    if not data:
        raise ProposalError(f"ledger key file {target} is empty")
    _FILE_KEY = data
    return target


def _key() -> bytes:
    if _FILE_KEY is not None:
        return _FILE_KEY
    for name in KEY_ENV:
        value = os.environ.get(name)
        if value:
            return value.encode("utf-8")
    raise ProposalError(f"no ledger key: set {KEY_ENV[0]} (or {KEY_ENV[1]}); refusing to act unrecorded")


def _mac(key: bytes, body: dict[str, Any]) -> str:
    # Signs canonical JSON rather than reusing cli/signed_ledger's '|'-joined fields, whose delimiter
    # ambiguity let a crafted field forge another row's signature.
    return hmac.new(key, json.dumps(body, sort_keys=True).encode("utf-8"), hashlib.sha256).hexdigest()


def _signed(key: bytes, body: dict[str, Any]) -> str:
    return json.dumps(dict(body, mac=_mac(key, body)), sort_keys=True)


def _verified(key: bytes, line: str) -> dict[str, Any] | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict) or "mac" not in row:
        return None
    mac = row.pop("mac")
    return row if hmac.compare_digest(str(mac), _mac(key, row)) else None


def _locked_append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def record(root: Path, entry: dict[str, Any]) -> None:
    """Append a signed, chained ledger row and re-sign the head anchor (under one exclusive lock).

    Args:
        root: The project root.
        entry: The row body (``action``, ``agent``, ...); ``time`` and ``prev`` are added.

    Returns:
        None.

    Raises:
        ProposalError: No ledger key is configured.
    """
    key = _key()
    ledger, head = root / LEDGER_REL, root / HEAD_REL
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            lines = fh.read().splitlines()
            prev = hashlib.sha256(lines[-1].encode("utf-8")).hexdigest() if lines else ""
            line = _signed(key, dict(entry, time=datetime.now(UTC).isoformat(timespec="seconds"), prev=prev))
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            anchor = {"count": len(lines) + 1, "last": hashlib.sha256(line.encode("utf-8")).hexdigest()}
            _atomic_write_text(head, _signed(key, anchor) + "\n")
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def verify_ledger(root: Path) -> list[str]:
    """Check every row's signature and chain, and the signed head anchor.

    Args:
        root: The project root.

    Returns:
        One problem per failure (empty when intact, or when there is no ledger and no head).

    Raises:
        ProposalError: No ledger key is configured.
    """
    key = _key()
    ledger, head = root / LEDGER_REL, root / HEAD_REL
    if not ledger.exists() and not head.exists():
        # Dispatches are always recorded, so dispatch records without a ledger mean the ledger was removed.
        return ["ledger and head are missing but dispatch records exist (ledger removed)"] \
            if (root / DISPATCH_REL).exists() else []
    problems: list[str] = []
    lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    prev = ""
    for number, line in enumerate(lines, 1):
        row = _verified(key, line)
        if row is None:
            problems.append(f"row {number}: signature invalid (edited, or written without the key)")
        elif row.get("prev") != prev:
            problems.append(f"row {number}: chain broken (a row was removed or reordered)")
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
    anchor = _verified(key, head.read_text(encoding="utf-8").strip()) if head.exists() else None
    if anchor is None:
        problems.append("head anchor missing or its signature invalid")
    elif anchor.get("count") != len(lines) or anchor.get("last") != (prev or None):
        problems.append(f"head anchor says {anchor.get('count')} rows; the ledger has {len(lines)} (truncated or appended unsigned)")
    return problems


def _nonce_id(key: bytes, nonce: str) -> str:
    return hmac.new(key, b"dispatch:" + nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def _dispatch_rows(key: bytes, root: Path) -> list[dict[str, Any]]:
    path = root / DISPATCH_REL
    rows = []
    for line in (path.read_text(encoding="utf-8").splitlines() if path.exists() else []):
        row = _verified(key, line)
        if row is not None:
            rows.append(row)
    return rows


def issue_dispatch(root: Path, agent: str, *, ttl_hours: int = DISPATCH_TTL_HOURS,
                   max_uses: int = DISPATCH_MAX_USES) -> str:
    """Record a signed dispatch for ``agent`` and return its nonce for the agent's task.

    Only ``HMAC(key, nonce)`` is stored, so reading ``.agentteams/`` reveals no usable nonce. Expired dispatches
    and their use rows are compacted away on each issue.

    Args:
        root: The project root.
        agent: The agent being dispatched.
        ttl_hours: How long the nonce stays valid.
        max_uses: How many artifacts it may submit.

    Returns:
        The nonce.

    Raises:
        ProposalError: No ledger key is configured, or the slug is malformed.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", agent or ""):
        raise ProposalError(f"malformed agent slug: {agent!r}")
    key, nonce = _key(), secrets.token_hex(16)
    now = datetime.now(UTC)
    path = root / DISPATCH_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with (root / DISPATCH_LOCK_REL).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            rows = [(line, _verified(key, line)) for line in text.splitlines()]
            live_ids = {r["id"] for _l, r in rows
                        if r and "expires" in r and datetime.fromisoformat(r["expires"]) >= now}
            # Keep unexpired dispatches and the use rows that count against them; drop the rest.
            live = [line for line, r in rows if r and (r.get("id") in live_ids or r.get("use") in live_ids)]
            live.append(_signed(key, {"id": _nonce_id(key, nonce), "agent": agent, "max_uses": max_uses,
                                      "expires": (now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")}))
            _atomic_write_text(path, "\n".join(live) + "\n")
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    record(root, {"action": "issue-dispatch", "agent": agent, "id": _nonce_id(key, nonce)[:12]})
    return nonce


def agent_for(root: Path, nonce: Any, *, consume: bool = True) -> str:
    """Return the agent a dispatch nonce was issued to, counting one use against it.

    Args:
        root: The project root.
        nonce: The nonce the artifact carries.
        consume: Record a use (``False`` for dry runs).

    Returns:
        The agent slug from the signed dispatch record.

    Raises:
        ProposalError: The nonce is missing, unknown, forged, expired or used up.
    """
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ProposalError("artifact carries no valid dispatch nonce (dispatch the agent with --issue-dispatch)")
    key = _key()
    ident = _nonce_id(key, nonce)
    (root / DISPATCH_LOCK_REL).parent.mkdir(parents=True, exist_ok=True)
    with (root / DISPATCH_LOCK_REL).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # count and append under one lock: the cap cannot be raced
        try:
            rows = _dispatch_rows(key, root)
            dispatch = next((r for r in rows if r.get("id") == ident and "agent" in r), None)
            if dispatch is None:
                raise ProposalError("unknown dispatch nonce; refused")
            if datetime.fromisoformat(dispatch["expires"]) < datetime.now(UTC):
                raise ProposalError("dispatch nonce has expired; re-dispatch the agent")
            used = sum(1 for r in rows if r.get("use") == ident)
            if used >= int(dispatch.get("max_uses", DISPATCH_MAX_USES)):
                raise ProposalError(f"dispatch nonce used {used} times (its limit); re-dispatch the agent")
            if consume:
                _locked_append(root / DISPATCH_REL, _signed(key, {"use": ident}))
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return str(dispatch["agent"])


# --- paths --------------------------------------------------------------------------------------


def _rel_inside(root: Path, path: Any) -> str:
    """Return ``path`` as a root-relative POSIX path, refusing anything that resolves outside the root."""
    if not isinstance(path, str) or not path or path.startswith(("/", "~")) or "\\" in path:
        raise ProposalError(f"path must be a non-empty root-relative POSIX path: {path!r}")
    real_root = os.path.realpath(root)
    real = os.path.realpath(os.path.join(real_root, path))
    if real != real_root and not real.startswith(real_root + os.sep):
        raise ProposalError(f"path {path!r} resolves outside the project; refused")
    rel = os.path.relpath(real, real_root).replace(os.sep, "/")
    if rel == ".":
        raise ProposalError("path must name a file, not the project root")
    return rel


def _protected(rel: str, patterns: list[str]) -> str | None:
    folded = rel.lower()
    for p in patterns:
        pat = p.lower().rstrip("/")
        if fnmatch.fnmatch(folded, pat) or (not any(c in pat for c in "*?[") and folded.startswith(pat + "/")):
            return p
    return None


def _check_destination(root: Path, path: Any, policy: Policy, agent: str) -> str:
    rel = _rel_inside(root, path)
    plane = control_plane_of(rel, platform="darwin")  # case-folded everywhere: refuse on any filesystem
    if plane:
        raise ProposalError(f"{rel} is inside the project control plane ({plane}); refused")
    if policy.brief_rel and rel.lower() == policy.brief_rel.lower():
        raise ProposalError(f"{rel} is the brief that defines this policy; refused (C-3)")
    hit = _protected(rel, policy.protected_paths)
    if hit:
        raise ProposalError(f"{rel} is a protected path ({hit}); change it only through its own script")
    scopes = (policy.agent_policies.get(agent) or {}).get("write_scopes") or []
    if not _in_scope(rel, scopes):
        raise ProposalError(f"{rel} is outside {agent}'s write_scopes {scopes}; refused")
    return rel


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


# --- execution environment and gates ------------------------------------------------------------


def _scrubbed_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in SAFE_ENV if k in os.environ}
    env["PATH"] = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep) if p and os.path.isabs(p)
    )
    return env


def _resolve_program(root: Path, program: str, env: dict[str, str]) -> str:
    """Resolve ``argv[0]`` on the sanitized PATH; refuse relative names and programs inside the project."""
    if "/" in program and not os.path.isabs(program):
        raise ProposalError(f"argv[0] {program!r} is a relative path; refused")
    resolved = program if os.path.isabs(program) else shutil.which(program, path=env.get("PATH", ""))
    if not resolved:
        raise ProposalError(f"argv[0] {program!r} was not found on the sanitized PATH")
    real, real_root = os.path.realpath(resolved), os.path.realpath(root)
    if real == real_root or real.startswith(real_root + os.sep):
        raise ProposalError(f"argv[0] {program!r} resolves inside the project ({real}); refused")
    return resolved


def _gates_for(rel: str, requested: Any, policy: Policy) -> list[str]:
    if not (isinstance(requested, list) and all(isinstance(g, str) for g in requested)):
        raise ProposalError("gates must be a list of gate names")
    unknown = [g for g in requested if g not in policy.gates]
    if unknown:
        raise ProposalError(f"unknown gate(s) requested: {', '.join(unknown)}")
    matching = [n for n, g in policy.gates.items()
                if g.get("glob") and fnmatch.fnmatch(rel.lower(), str(g["glob"]).lower())]
    return list(dict.fromkeys(list(requested) + matching))


def _run_gates(root: Path, rel: str, content: str, names: list[str], policy: Policy,
               *, confine: bool = False) -> list[dict[str, Any]]:
    """Run each gate on a temporary copy of the proposed content; raise on the first failure.

    With *confine*, each gate runs in the OS sandbox: it may write only its temp dir and run only its
    ``exec`` paths (default: its own program and a shebang interpreter).
    """
    results: list[dict[str, Any]] = []
    if not names:
        return results
    env = _scrubbed_env()
    sandbox = _sandbox_for(policy) if confine else None
    with tempfile.TemporaryDirectory(prefix="agentteams-proposal-") as tmp:
        env = {**env, "TMPDIR": tmp} if confine else env
        candidate = Path(tmp) / Path(rel).name
        candidate.write_text(content, encoding="utf-8")
        for name in names:
            argv = [str(candidate) if a == "{file}" else rel if a == "{path}" else a for a in policy.gates[name]["argv"]]
            argv[0] = _resolve_program(root, argv[0], env)
            if sandbox:
                gate_exec = _confinement.gate_exec_paths(policy.gates[name], argv[0])
                try:
                    _confinement.check_roots(root, gate_exec, [], Path(tmp))
                except _confinement.ConfinementError as exc:
                    raise ProposalError(f"gate {name}: {exc}") from exc
                argv = _confinement.wrap(argv, sandbox=sandbox, root=root, cwd=root, exec_paths=gate_exec,
                                         write_roots=[], tmp_dir=Path(tmp))
            try:
                proc = subprocess.run(argv, cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, timeout=GATE_TIMEOUT, shell=False)
            except (OSError, subprocess.SubprocessError) as exc:
                raise ProposalError(f"gate {name} could not run: {exc}") from exc
            results.append({"gate": name, "exit": proc.returncode,
                            **({"confined": sandbox or "unconfined-opt-out"} if confine else {})})
            if proc.returncode != 0:
                tail = (proc.stdout + proc.stderr).strip()[-600:]
                raise ProposalError(f"gate {name} refused the proposed content (exit {proc.returncode}): {tail}")
    return results


# --- artifacts ----------------------------------------------------------------------------------


def _check_shape(artifact: Any, kind: str) -> None:
    if not isinstance(artifact, dict) or artifact.get("kind") != kind:
        raise ProposalError(f"not a {kind}")
    required, optional = _KEYS[kind]
    missing, extra = required - set(artifact), set(artifact) - required - optional
    if missing or extra:
        raise ProposalError(f"{kind}: missing {sorted(missing)}, unknown {sorted(extra)}")


def _identity(root: Path, artifact: dict[str, Any], *, dry_run: bool = False) -> str:
    agent = agent_for(root, artifact.get("dispatch"), consume=not dry_run)
    claimed = artifact.get("agent")
    if claimed is not None and claimed != agent:
        raise ProposalError(f"artifact claims agent {claimed!r} but was dispatched to {agent!r}; refused")
    return agent


def _check_base(root: Path, rel: str, base: Any) -> Path:
    if not (base == "absent" or (isinstance(base, str) and _HEX64.match(base))):
        raise ProposalError('base_sha256 must be a sha256 hex digest or "absent"')
    target = Path(os.path.realpath(root)) / rel
    current = _sha256(target)
    if (base == "absent") != (current is None) or (current is not None and current != base):
        raise ProposalError(f"{rel} changed since the agent read it (stale base); re-dispatch with the current file")
    if current is not None:
        try:
            target.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProposalError(f"{rel} is not UTF-8 text; proposals carry text only") from exc
    return target


def apply_proposal(artifact: dict[str, Any], *, root: Path, policy: Policy, dry_run: bool = False,
                   allow_gates: bool = True, confine: bool = False) -> dict[str, Any]:
    """Validate, gate and (unless ``dry_run``) apply one change or deletion proposal.

    Args:
        artifact: A parsed ``change-proposal`` or ``delete-proposal``.
        root: The project root.
        policy: The team's policy (:func:`load_policy`).
        dry_run: Run every check and gate (gates execute) but change nothing.
        allow_gates: When False, refuse a proposal that any gate would check rather than run the gate. The
            out-of-session runner confines gates instead (``confine=True``). An unconfined gate is a child
            of the key holder and can load session-writable code.
        confine: Run gates in the OS sandbox (the runner does). Refuses when none is usable, unless the
            brief's logged ``allow_unconfined_runs`` opt-out is set.

    Returns:
        ``{agent, path, base_sha256, new_sha256, gates, written}`` (``new_sha256`` is ``None`` for a deletion).

    Raises:
        ProposalError: Any check or gate refuses; nothing changes. The refusal is recorded in the ledger.
    """
    kind = artifact.get("kind") if isinstance(artifact, dict) else None
    agent = "?"
    try:
        _check_shape(artifact, kind if kind in ("change-proposal", "delete-proposal") else "change-proposal")
        agent = _identity(root, artifact, dry_run=dry_run)
        rationale = artifact.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ProposalError("a non-empty rationale is required")
        rel = _check_destination(root, artifact.get("path"), policy, agent)
        if kind == "delete-proposal":
            if artifact["base_sha256"] == "absent":
                raise ProposalError("a deletion needs the hash of the file being deleted")
            target = _check_base(root, rel, artifact["base_sha256"])
            if not dry_run:
                _check_destination(root, rel, policy, agent)  # re-resolve immediately before acting
                _check_base(root, rel, artifact["base_sha256"])
                record(root, {"action": "delete-proposal", "agent": agent, "path": rel,  # write-ahead
                              "base": artifact["base_sha256"], "rationale": rationale.strip()[:300]})
                target.unlink()
            return {"agent": agent, "path": rel, "base_sha256": artifact["base_sha256"], "new_sha256": None,
                    "gates": [], "written": not dry_run}
        content = artifact.get("content")
        if not isinstance(content, str):
            raise ProposalError("content must be a string")
        size = len(content.encode("utf-8"))
        if size > policy.size_cap:
            raise ProposalError(f"content is {size} bytes, over the {policy.size_cap}-byte cap; review it directly")
        _check_base(root, rel, artifact["base_sha256"])
        gate_names = _gates_for(rel, artifact.get("gates") or [], policy)
        if gate_names and not allow_gates:
            raise ProposalError(f"gate(s) {', '.join(gate_names)} can't run unconfined beside the ledger key "
                                "(run them confined: confine=True)")
        gate_results = _run_gates(root, rel, content, gate_names, policy, confine=confine)
        new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if not dry_run:
            rel = _check_destination(root, rel, policy, agent)  # re-resolve after the gates ran
            target = _check_base(root, rel, artifact["base_sha256"])
            record(root, {"action": "apply-proposal", "agent": agent, "path": rel, "base": artifact["base_sha256"],
                          "new": new_hash, "gates": gate_results, "rationale": rationale.strip()[:300]})  # write-ahead
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(target, content)
        return {"agent": agent, "path": rel, "base_sha256": artifact["base_sha256"], "new_sha256": new_hash,
                "gates": gate_results, "written": not dry_run}
    except ProposalError as exc:
        if not dry_run:
            _record_refusal(root, "apply-proposal", agent, str(exc))
        raise


def _record_refusal(root: Path, action: str, agent: str, reason: str) -> None:
    """Log a refusal when a ledger key exists (without one, the refusal is the 'no key' error itself)."""
    if _FILE_KEY is not None or any(os.environ.get(name) for name in KEY_ENV):
        record(root, {"action": f"{action}-refused", "agent": agent, "reason": reason[:300]})


# --- command requests ---------------------------------------------------------------------------


def _matching_entry(argv: list[str], agent: str, policy: Policy) -> dict[str, Any] | None:
    for entry in (policy.agent_policies.get(agent) or {}).get("commands") or []:
        prefix, patterns = list(entry["prefix"]), list(entry.get("args") or [])
        rest = argv[len(prefix):]
        if argv[: len(prefix)] != prefix:
            continue
        if any(".." in a.replace("\\", "/").split("/") for a in rest):
            continue  # a parent-path segment is never an allowed argument, whatever the pattern admits
        if entry.get("variadic") and patterns:
            fixed, last = patterns[:-1], patterns[-1]
            ok = len(rest) >= len(patterns) and all(re.fullmatch(p, a) for p, a in zip(fixed, rest)) \
                and all(re.fullmatch(last, a) for a in rest[len(fixed):])
        else:
            ok = len(rest) == len(patterns) and all(re.fullmatch(p, a) for p, a in zip(patterns, rest))
        if ok:
            return entry
    return None


#: Control-plane locations watched during a command (``.git`` limited to its persistence vectors: git itself
#: rewrites the index and objects during ``git status``).
_WATCHED = (".agentteams", ".claude", ".goose", ".codex", ".github/agents", ".github/hooks", "sandbox")
#: git persistence vectors, resolved with ``git rev-parse --git-path`` (linked worktrees, ``core.hooksPath``).
_GIT_WATCHED = ("hooks", "config", "config.worktree", "info")


def _git(root: Path, env: dict[str, str], *args: str) -> bytes | None:
    try:
        proc = subprocess.run(["git", *args], cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _snapshot(root: Path, env: dict[str, str]) -> dict[str, Any] | None:
    """Fingerprint git-dirty/untracked files (content hash) and watched control-plane files (stat).

    Returns ``None`` when ``root`` is not inside a git worktree; :func:`run_request` then refuses to run.
    Paths are decoded with ``os.fsdecode``, so non-UTF-8 names cannot break the comparison.
    """
    prefix_out = _git(root, env, "rev-parse", "--show-prefix")
    status = _git(root, env, "status", "--porcelain=v1", "--untracked-files=all", "-z", "--", ".")
    if prefix_out is None or status is None:
        return None
    prefix = os.fsdecode(prefix_out).strip()
    tokens, files, i = status.split(b"\0"), set(), 0
    while i < len(tokens):
        entry = tokens[i]
        if len(entry) > 3:
            files.add(os.fsdecode(entry[3:]))
            if entry[:1] in (b"R", b"C") or entry[1:2] in (b"R", b"C"):
                i += 1  # the next token is the rename/copy source
                if i < len(tokens) and tokens[i]:
                    files.add(os.fsdecode(tokens[i]))
        i += 1
    snap: dict[str, Any] = {}
    for f in files:
        rel = f[len(prefix):] if prefix and f.startswith(prefix) else f
        snap[rel] = _sha256(root / rel)
    bases = [root / w for w in _WATCHED]
    for name in _GIT_WATCHED:
        resolved = _git(root, env, "rev-parse", "--path-format=absolute", "--git-path", name)
        text = os.fsdecode(resolved or b"").strip()
        path = Path(text)
        # Fail closed: an unresolvable or garbled path (e.g. a git too old for --path-format echoing the flag)
        # would silently drop the watch.
        if not text or "\n" in text or not path.is_absolute() or not path.parent.is_dir():
            return None
        bases.append(path)
    for base in bases:
        paths = [base] if base.is_file() else (p for p in base.rglob("*") if p.is_file()) if base.is_dir() else []
        for p in paths:
            st = p.lstat()
            try:
                key_name = p.relative_to(root).as_posix()
            except ValueError:
                key_name = str(p)  # a git dir outside the project (linked worktree, core.hooksPath)
            # ctime cannot be set by the user: a same-size rewrite with its mtime restored still shows.
            snap[key_name] = ("stat", st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino)
    return snap


def run_request(artifact: dict[str, Any], *, root: Path, policy: Policy, dry_run: bool = False,
                confine: bool = False) -> dict[str, Any]:
    """Validate and (unless ``dry_run``) run one command request.

    Args:
        artifact: The parsed command-request JSON.
        root: The project root.
        policy: The team's policy.
        dry_run: Check everything (gates on ``stdin_from_content`` execute) but run nothing.
        confine: Run the command (and its stdin gates) in the OS sandbox from the agent's
            ``confined_programs`` entry (the runner does). Refused without such an entry, when the
            program lies outside its ``exec`` paths, or when no sandbox is usable and the brief has no logged
            ``allow_unconfined_runs`` opt-out. Any process still alive after the command exits is killed
            before the write check, and the run fails.

    Returns:
        ``{agent, argv, exit, stdout, stderr, undeclared_writes, ran}``. Outside a git worktree the command
        is refused, so ``undeclared_writes`` is always a list.

    Raises:
        ProposalError: The request is malformed or not allowed (nothing runs), or the command wrote outside its
            declared writes (it ran; the run fails). Refusals and failures are recorded in the ledger.
    """
    agent = "?"
    try:
        _check_shape(artifact, "command-request")
        agent = _identity(root, artifact, dry_run=dry_run)
        argv, purpose = artifact.get("argv"), artifact.get("purpose")
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
            raise ProposalError("argv must be a non-empty list of strings (never a shell string)")
        if not isinstance(purpose, str) or not purpose.strip():
            raise ProposalError("a non-empty purpose is required")
        entry = _matching_entry(argv, agent, policy)
        if entry is None:
            raise ProposalError(f"{argv!r} is not on {agent}'s command allowlist; refused")
        cwd_rel = artifact.get("cwd") or "."
        if not isinstance(cwd_rel, str) or cwd_rel != (entry.get("cwd") or "."):
            raise ProposalError(f"cwd must be the entry's pinned cwd {entry.get('cwd') or '.'!r}")
        cwd = root if cwd_rel == "." else root / _rel_inside(root, cwd_rel)
        expected = artifact.get("expected_writes") or []
        if not (isinstance(expected, list) and all(isinstance(p, str) for p in expected)):
            raise ProposalError("expected_writes must be a list of paths")
        expected_rel = set()
        for path in expected:
            rel = _rel_inside(root, path)
            allowed = entry.get("writes") or []
            if not any(fnmatch.fnmatch(rel.lower(), str(g).lower()) for g in allowed):
                _check_destination(root, rel, policy, agent)  # else it must be an ordinary scoped write
            elif control_plane_of(rel, platform="darwin"):
                raise ProposalError(f"{rel} is inside the control plane; no command may declare it")
            elif policy.brief_rel and rel.lower() == policy.brief_rel.lower():
                raise ProposalError(f"{rel} is the brief that defines this policy; refused (C-3)")
            expected_rel.add(rel)
        env = _scrubbed_env()
        program = _resolve_program(root, argv[0], env)
        jail = None
        if confine:
            jail = policy.confined.get(agent)
            if jail is None:
                raise ProposalError(f"{agent} has no confined_programs entry; the runner runs nothing unconfined")
            if not _confinement.exec_allows(program, jail["exec"]):
                raise ProposalError(f"{program} is outside {agent}'s confined exec paths; refused")
            for rel in jail["write"]:
                prefix = rel.strip("/").lower() + "/"
                hit = _protected(prefix + "x", policy.protected_paths) or any(
                    str(p).replace("\\", "/").lower().startswith(prefix) for p in policy.protected_paths) or (
                    policy.brief_rel and _in_scope(policy.brief_rel, [prefix]))
                if hit:
                    raise ProposalError(f"confined_programs.{agent}.write {rel!r} covers a protected path or the "
                                        "brief; changes there would count as declared")
            try:  # before any snapshot or ledger row: a swapped root must refuse, not trip the write check
                _confinement.check_roots(root, jail["exec"], jail["write"])
            except _confinement.ConfinementError as exc:
                raise ProposalError(f"confined_programs.{agent}: {exc}") from exc
        stdin_bytes = None
        spec = artifact.get("stdin_from_content")
        if spec is not None:
            if not (isinstance(spec, dict) and set(spec) == {"path", "content"} and isinstance(spec["path"], str)
                    and isinstance(spec["content"], str)):
                raise ProposalError("stdin_from_content must be exactly {path, content}")
            if "stdin_gates" not in entry:
                raise ProposalError("this command entry accepts no stdin content (no stdin_gates registered)")
            _run_gates(root, _rel_inside(root, spec["path"]), spec["content"], list(entry["stdin_gates"]), policy,
                       confine=confine)
            stdin_bytes = spec["content"].encode("utf-8")
        if dry_run:
            return {"agent": agent, "argv": argv, "exit": None, "stdout": "", "stderr": "",
                    "undeclared_writes": [], "ran": False}
        before = _snapshot(root, env)
        if before is None:
            raise ProposalError("the project is not a git worktree, or git's own paths could not be resolved; "
                                "refusing to run unchecked (fail-closed)")
        sandbox = _sandbox_for(policy) if jail is not None else None
        confined_as = sandbox or ("unconfined-opt-out" if jail is not None else None)
        record(root, {"action": "run-request-start", "agent": agent, "argv": argv, "purpose": purpose.strip()[:300],
                      **({"confined": confined_as} if confined_as else {})})
        before = _snapshot(root, env)  # re-taken after the write-ahead row, so only the command's changes count
        timed_out, group_killed, survivors = False, False, False
        run_tmp = tempfile.mkdtemp(prefix="agentteams-run-") if jail is not None else None
        command = [program, *argv[1:]]
        if run_tmp is not None:
            env = {**env, "TMPDIR": run_tmp}
            try:
                checked_roots = _confinement.check_roots(root, jail["exec"], jail["write"], Path(run_tmp))
            except _confinement.ConfinementError as exc:
                shutil.rmtree(run_tmp, ignore_errors=True)
                raise ProposalError(f"confined_programs.{agent}: {exc}") from exc
            if sandbox:
                command = _confinement.wrap(command, sandbox=sandbox, root=root, cwd=cwd, exec_paths=jail["exec"],
                                            write_roots=checked_roots, tmp_dir=Path(run_tmp))
        try:
            # Own process group, so a timeout kills everything the command started before the final check.
            child = subprocess.Popen(command, cwd=cwd, env=env,
                                     stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                     start_new_session=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProposalError(f"command could not run: {exc}") from exc
        try:
            out, err = child.communicate(stdin_bytes, timeout=COMMAND_TIMEOUT)
            proc = subprocess.CompletedProcess(argv, child.returncode, out, err)
        except subprocess.TimeoutExpired:
            # The command ran (partly): its writes and any ledger tampering must still be checked.
            timed_out = True
            try:
                os.killpg(child.pid, 9)
                group_killed = True
            except ProcessLookupError:
                group_killed = False  # the group had already exited; recorded on the timeout row
            out, err = child.communicate()
            proc = subprocess.CompletedProcess(argv, -9, out or b"", err or b"")
        if jail is not None:
            # Anything the command left running could write after the snapshot: kill the group, fail the run.
            try:
                os.killpg(child.pid, 9)
                survivors = True
            except ProcessLookupError:
                survivors = False  # the group is empty: nothing outlived the command
            if run_tmp:
                shutil.rmtree(run_tmp, ignore_errors=True)
        after = _snapshot(root, env)
        if after is None:
            raise LedgerTamperedError("the git worktree vanished during the command; nothing more is recorded")
        changed = {f for f, h in after.items() if before.get(f, "∅") != h} | (set(before) - set(after))
        tampered = sorted(f for f in changed if f in (LEDGER_REL, HEAD_REL, DISPATCH_REL))
        if tampered:
            # Never chain onto a ledger the command altered: stop and leave it for the operator.
            raise LedgerTamperedError(f"the command changed {', '.join(tampered)}; nothing more is recorded")
        # Under confinement, the operator-declared write roots (build dirs) bound what the kernel let the command
        # write, so changes inside them count as declared; anything else changed is still undeclared.
        # Only when a sandbox actually ran: under the unconfined opt-out nothing at the OS level bounded them.
        roots = [w.strip("/") + "/" for w in jail["write"]] if jail is not None and sandbox else []
        def exempt(f: str) -> bool:  # inside a root, and never a protected file or the brief (any glob shape)
            return bool(roots) and f.startswith(tuple(roots)) and not _protected(f, policy.protected_paths) \
                and not (policy.brief_rel and f.lower() == policy.brief_rel.lower())

        undeclared = sorted(f for f in changed if f not in expected_rel and not exempt(f))
        if timed_out:
            record(root, {"action": "run-request-timeout", "agent": agent, "argv": argv,
                          "group_killed": group_killed, "undeclared_writes": undeclared, "purpose": purpose.strip()[:300]})
            raise UndeclaredWritesError(
                f"command timed out after {COMMAND_TIMEOUT}s"
                + (f"; it wrote outside its declared writes: {', '.join(undeclared)}" if undeclared else ""),
                {"agent": agent, "argv": argv, "exit": -9, "stdout": proc.stdout.decode("utf-8", "replace"),
                 "stderr": proc.stderr.decode("utf-8", "replace"), "undeclared_writes": undeclared, "ran": True})
        record(root, {"action": "run-request", "agent": agent, "argv": argv, "exit": proc.returncode,
                      "undeclared_writes": undeclared, "purpose": purpose.strip()[:300],
                      **({"confined": confined_as, "survivors_killed": survivors} if confined_as else {})})
        result = {"agent": agent, "argv": argv, "exit": proc.returncode,
                  "stdout": proc.stdout.decode("utf-8", "replace"), "stderr": proc.stderr.decode("utf-8", "replace"),
                  "undeclared_writes": undeclared, "ran": True}
        if undeclared:
            raise UndeclaredWritesError(f"command wrote outside its declared writes: {', '.join(undeclared)}", result)
        if survivors:
            raise UndeclaredWritesError("command left processes running after it exited; they were killed and "
                                        "the run fails", result)
        return result
    except (UndeclaredWritesError, LedgerTamperedError):
        raise  # recorded in the run-request row, or deliberately not recorded (tampered ledger)
    except ProposalError as exc:
        if not dry_run:
            _record_refusal(root, "run-request", agent, str(exc))
        raise
