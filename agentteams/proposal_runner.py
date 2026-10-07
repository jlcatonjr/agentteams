"""proposal_runner.py — the out-of-session runner for the orchestrator-only-writes pilot (P4a).

Under ``write_policy: "orchestrator-only"`` the orchestrator's session never holds the ledger key, and it
can't confine commands itself: a sandboxed process can't apply a nested Seatbelt profile. So an operator
starts ``agentteams --serve-requests`` *outside* every agent session. It is the only holder of the key,
which it reads from a 0600 key file (:func:`proposals.use_key_file`). It applies queued artifacts, and the
orchestrator's CLI talks to it through a queue:

* ``.agentteams-queue/requests/<id>.json``: written by the orchestrator's CLI. The session can write here.
* ``.agentteams/queue-results/<id>.json``: written by the runner. Sessions under the switch can't write
  ``.agentteams/``, so a result can't be forged.
* ``.agentteams-queue/acks/<id>``: written by the CLI once it has read a result. The runner then deletes the
  result, so a raw dispatch nonce doesn't linger where another agent could read it.

Queue content is data (C-4). Every request is re-validated by :mod:`agentteams.proposals`, exactly as a
direct CLI call is. The runner pins the project root, the brief and the policy at start, and stops serving
if the brief changes. Every command and gate it starts runs in the OS sandbox (:mod:`agentteams.confinement`,
P4b). With no usable sandbox it refuses, unless the brief's logged ``allow_unconfined_runs`` opt-out is set.

Stdlib only; integrity-pinned. POSIX only (``fcntl``, ``O_NOFOLLOW``).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from agentteams import confinement as _confinement
from agentteams import proposals as P
from agentteams.atomicio import FileTooLargeError, read_regular_nofollow, write_new_atomic

QUEUE_DIR_REL = ".agentteams-queue"
REQUESTS_REL = ".agentteams-queue/requests"
ACKS_REL = ".agentteams-queue/acks"
RESULTS_REL = ".agentteams/queue-results"
CLAIMED_REL = ".agentteams/queue-claimed"
LOCK_REL = ".agentteams/runner.lock"
HEARTBEAT_REL = ".agentteams/runner.heartbeat"

#: Request kinds the runner serves. Commands and gates run confined (P4b).
KINDS = ("issue-dispatch", "apply-proposal", "run-request", "verify-ledger")
REQUEST_MAX_BYTES = 2 * 1024 * 1024
HEARTBEAT_STALE_SECONDS = 15
RESULT_TTL_SECONDS = 300
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class RunnerError(P.ProposalError):
    """The runner can't serve, or the CLI found no runner answering."""


# --- small file helpers -------------------------------------------------------------------------


def _read_regular(path: Path, max_bytes: int) -> bytes:
    """Read a regular file without following a symlink, refusing anything larger than *max_bytes*."""
    try:
        return read_regular_nofollow(path, max_bytes)
    except ValueError as exc:
        raise RunnerError("request too large" if isinstance(exc, FileTooLargeError) else str(exc)) from exc


def _write_new(directory: Path, name: str, data: bytes) -> None:
    """Write *data* to ``directory/name`` atomically: an exclusive temp file, then a rename."""
    directory.mkdir(parents=True, exist_ok=True)
    write_new_atomic(directory, name, data)


def _brief_hash(brief_path: Path) -> str:
    return hashlib.sha256(brief_path.read_bytes()).hexdigest()


def _confined_hash(root: Path) -> str | None:
    """The operator confined file's hash now (None when absent); a custody failure counts as a change."""
    try:
        found = _confinement.read_confined_file(root)
    except (_confinement.ConfinementError, OSError):
        return "unreadable"
    return found[1] if found else None


def check_installed_readfs(root: Path) -> None:
    """Refuse to serve when the installed Goose read-only file server isn't the shipped one.

    Under the switch the server is installed at ``goose_tool_scoping.READFS_PROTECTED_PATH`` (session
    write-denied) and every recipe launches it. The runner is the trust anchor outside every session, so it
    checks that copy's sha256 against the integrity-pinned ``READFS_SHA256``. A team with no installed copy
    (no Goose readers) passes.

    Args:
        root: The project root.

    Returns:
        None.

    Raises:
        RunnerError: The installed copy is a symlink, not a regular file, or doesn't match the pinned hash.
    """
    # Local import: keeps the frameworks package off the runner's start-up path for teams without Goose.
    from agentteams.frameworks.goose_tool_scoping import READFS_PROTECTED_PATH, READFS_SHA256

    path = root / READFS_PROTECTED_PATH
    for parent in (path.parent.parent, path.parent):  # .agentteams and .agentteams/bin: a linked-in folder could
        if parent.is_symlink():                       # point at agent-writable space, so refuse it outright
            raise RunnerError(f"{parent.relative_to(root)} is a symlink; refusing to serve")
    if not path.exists() and not path.is_symlink():
        return
    try:
        data = _read_regular(path, 4 * 1024 * 1024)
    except (OSError, RunnerError) as exc:
        raise RunnerError(f"{READFS_PROTECTED_PATH} is not a regular file ({exc}); refusing to serve") from exc
    if hashlib.sha256(data).hexdigest() != READFS_SHA256:
        raise RunnerError(f"{READFS_PROTECTED_PATH} doesn't match the shipped read-only file server; refusing to "
                          "serve. Re-render the team with this agentteams install to reinstall it")


# --- the runner ---------------------------------------------------------------------------------


class Runner:
    """Serve one project's queue. Construct it, then call :meth:`serve_once` or :meth:`serve_forever`."""

    def __init__(self, root: Path, brief_path: Path, policy: P.Policy, *, key_file: str | None = None) -> None:
        """Pin the project, the brief and the policy, take custody of the key and the runner lock.

        Args:
            root: The project root.
            brief_path: The project brief the policy came from (its hash is pinned).
            policy: The policy loaded from that brief.
            key_file: The ledger key file; defaults as in :func:`proposals.use_key_file`.

        Raises:
            RunnerError: When the brief is unreadable or doesn't set the switch, a queue directory is a
                symlink, or another runner holds the lock.
            ProposalError: When the key file is unusable.
        """
        self.root = root.resolve()
        self.brief_path = brief_path.resolve()
        self.policy = policy
        try:
            raw = self.brief_path.read_bytes()  # one read: the pinned hash and the switch check see the same bytes
            switch = json.loads(raw).get("write_policy")
        except (OSError, ValueError, AttributeError) as exc:
            raise RunnerError(f"cannot read the brief: {exc}") from exc
        self.brief_sha = hashlib.sha256(raw).hexdigest()
        if switch != "orchestrator-only":
            # Without the switch, session sandboxes don't write-deny .agentteams/, so results could be forged.
            raise RunnerError('the brief does not set write_policy "orchestrator-only"; the runner serves only '
                              "teams under the switch")
        for rel in (".agentteams", QUEUE_DIR_REL, REQUESTS_REL, ACKS_REL, RESULTS_REL, CLAIMED_REL):
            if (self.root / rel).is_symlink():
                raise RunnerError(f"{rel} is a symlink; refusing to serve")
        leaked = [name for name in P.KEY_ENV if os.environ.get(name)]
        if leaked:
            # A confined child can read this process's exec-time environment (KERN_PROCARGS2): never hold a key
            # there, even an unused one.
            raise RunnerError(f"unset {', '.join(leaked)} before starting the runner; it reads the key from its "
                              "key file only, and a confined child could read this process's environment")
        P.use_key_file(key_file)
        (self.root / ".agentteams").mkdir(mode=0o700, exist_ok=True)
        self._lock_fd = os.open(self.root / LOCK_REL, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._lock_fd)
            raise RunnerError("another runner is already serving this project") from exc
        for rel in (REQUESTS_REL, ACKS_REL):
            (self.root / rel).mkdir(parents=True, exist_ok=True)  # session-writable by design
        for rel in (RESULTS_REL, CLAIMED_REL):
            (self.root / rel).mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            check_installed_readfs(self.root)
        except RunnerError:
            self.close()
            raise
        self._heartbeat()

    def close(self) -> None:
        """Release the runner lock and remove the heartbeat (the lock file itself stays, on purpose).

        Raises:
            Nothing.
        """
        (self.root / HEARTBEAT_REL).unlink(missing_ok=True)
        os.close(self._lock_fd)

    def _heartbeat(self) -> None:
        _write_new(self.root / ".agentteams", Path(HEARTBEAT_REL).name, str(time.time()).encode())

    def _handle(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = request.get("kind")
        if kind == "issue-dispatch":
            return {"nonce": P.issue_dispatch(self.root, str(request.get("agent") or ""))}
        if kind == "apply-proposal":
            return P.apply_proposal(request.get("artifact"), root=self.root, policy=self.policy,
                                    dry_run=bool(request.get("dry_run")), confine=True)
        if kind == "run-request":
            return P.run_request(request.get("artifact"), root=self.root, policy=self.policy,
                                 dry_run=bool(request.get("dry_run")), confine=True)
        if kind == "verify-ledger":
            return {"problems": P.verify_ledger(self.root)}
        raise RunnerError("unknown request kind")

    def _open_session_dir(self, rel: str) -> int:
        """Open a session-writable queue dir without following a symlink anywhere in its last component."""
        try:
            return os.open(self.root / rel, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise RunnerError(f"{rel} is not a plain directory (a symlink?); refusing to serve") from exc

    def _serve_request(self, requests_fd: int, name: str) -> None:
        request_id = name[: -len(".json")]
        claimed = self.root / CLAIMED_REL / name
        claimed_fd = os.open(self.root / CLAIMED_REL, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:  # claim it relative to the opened dir: a swapped-in symlinked dir can't redirect the rename
            os.rename(name, name, src_dir_fd=requests_fd, dst_dir_fd=claimed_fd)
        finally:
            os.close(claimed_fd)
        result: dict[str, Any] = {"id": request_id, "ok": False}
        try:  # P5 measurement: how long the request waited in the queue, and how long serving it took
            result["queue_wait_ms"] = int((time.time() - os.stat(claimed, follow_symlinks=False).st_mtime) * 1000)
        except OSError:
            result["queue_wait_ms"] = None
        served_from = time.monotonic()
        try:
            try:
                request = json.loads(_read_regular(claimed, REQUEST_MAX_BYTES))
            except (OSError, ValueError) as exc:
                # Never echo request content: a request symlinked to a secret would leak it.
                raise RunnerError("unreadable request (not a regular JSON file)") from exc
            if not isinstance(request, dict) or request.get("kind") not in KINDS:
                raise RunnerError("unknown request kind")
            result["kind"] = request["kind"]
            result["result"] = self._handle(request)
            result["ok"] = True
        except P.UndeclaredWritesError as exc:
            result.update(error=str(exc), exit=3, result=exc.result)
        except P.ProposalError as exc:
            result.update(error=str(exc), exit=1)
        finally:
            claimed.unlink(missing_ok=True)
        result["serve_ms"] = int((time.monotonic() - served_from) * 1000)
        _write_new(self.root / RESULTS_REL, name, json.dumps(result).encode())

    def _expire_results(self) -> None:
        now = time.time()
        acks_fd = self._open_session_dir(ACKS_REL)
        try:
            acks = {e.name for e in os.scandir(acks_fd) if _ID_RE.match(e.name)}
            for path in (self.root / RESULTS_REL).glob("*.json"):
                if path.stem in acks or now - path.stat().st_mtime > RESULT_TTL_SECONDS:
                    path.unlink(missing_ok=True)
            for name in acks:
                if not (self.root / RESULTS_REL / f"{name}.json").exists():
                    os.unlink(name, dir_fd=acks_fd)  # relative to the opened dir: never outside it
        finally:
            os.close(acks_fd)

    def serve_once(self) -> int:
        """Serve every request queued now.

        Returns:
            How many requests were served.

        Raises:
            RunnerError: When the brief changed since start (the runner must be restarted).
        """
        if _brief_hash(self.brief_path) != self.brief_sha:
            raise RunnerError("the brief changed since the runner started; restart it to load the new policy")
        if _confined_hash(self.root) != self.policy.confined_file_sha:
            raise RunnerError("the operator confined_programs file changed since the runner started; restart it")
        # Re-checked every poll. This DETECTS a swapped server and stops serving; it doesn't stop Goose launching
        # it. Prevention is the session sandbox's write-deny on .agentteams/ (--check-wiring requires it live).
        check_installed_readfs(self.root)
        self._heartbeat()
        self._expire_results()
        served = 0
        requests_fd = self._open_session_dir(REQUESTS_REL)
        try:
            for entry in sorted(os.scandir(requests_fd), key=lambda e: e.name):
                if not (entry.name.endswith(".json") and _ID_RE.match(entry.name[: -len(".json")])):
                    continue  # temp files, stray names: ignored, never opened
                if not entry.is_file(follow_symlinks=False):
                    os.unlink(entry.name, dir_fd=requests_fd)
                    continue
                self._serve_request(requests_fd, entry.name)
                served += 1
        finally:
            os.close(requests_fd)
        return served

    def serve_forever(self, *, poll_seconds: float = 0.3, should_stop: Callable[[], bool] = lambda: False) -> None:
        """Serve until *should_stop* returns True or the brief changes.

        Args:
            poll_seconds: Delay between queue scans.
            should_stop: Checked after each scan.

        Returns:
            None, once *should_stop* returns True.

        Raises:
            RunnerError: When the brief changed since start.
        """
        while not should_stop():
            self.serve_once()
            time.sleep(poll_seconds)


# --- the client (the orchestrator's CLI) --------------------------------------------------------


def runner_alive(root: Path) -> bool:
    """Whether a runner has written a heartbeat for *root* recently.

    Args:
        root: The project root.

    Returns:
        True when the heartbeat is fresher than :data:`HEARTBEAT_STALE_SECONDS`.

    Raises:
        Nothing.
    """
    try:
        return time.time() - float((root / HEARTBEAT_REL).read_text()) < HEARTBEAT_STALE_SECONDS
    except (OSError, ValueError):
        return False


def enqueue(root: Path, request: dict[str, Any]) -> str:
    """Queue *request* for the runner and return its id.

    Args:
        root: The project root.
        request: ``{"kind": ..., ...}``. It never carries a root, brief or policy: the runner pins those.

    Returns:
        The request id.

    Raises:
        RunnerError: When no runner is serving *root*.
    """
    if not runner_alive(root):
        raise RunnerError(f"no runner is serving this project (no fresh {HEARTBEAT_REL}); the operator starts one "
                          "outside any agent session: agentteams --serve-requests --project <root> "
                          "--description <brief>")
    request_id = secrets.token_hex(16)
    _write_new(root / REQUESTS_REL, f"{request_id}.json", json.dumps(request).encode())
    return request_id


def wait_result(root: Path, request_id: str, *, timeout: float = 120.0) -> dict[str, Any]:
    """Wait for the runner's result for *request_id*, then acknowledge it so the runner deletes it.

    Args:
        root: The project root.
        request_id: From :func:`enqueue`.
        timeout: Seconds to wait.

    Returns:
        The result: ``{"id", "kind", "ok", "result"?, "error"?, "exit"?}``.

    Raises:
        RunnerError: On a bad id, a timeout, or a runner that stopped answering.
    """
    if not _ID_RE.match(request_id):
        raise RunnerError("bad request id")
    path = root / RESULTS_REL / f"{request_id}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            result = json.loads(_read_regular(path, REQUEST_MAX_BYTES * 2))
            _write_new(root / ACKS_REL, request_id, b"")
            return result
        if not runner_alive(root):
            raise RunnerError("the runner stopped answering; restart it and re-queue the request")
        time.sleep(0.2)
    raise RunnerError(f"no result for {request_id} within {timeout:.0f}s; wait longer with --wait-result")
