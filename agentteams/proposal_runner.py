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
import threading
import time
from pathlib import Path
from typing import Any, Callable

from agentteams import confinement as _confinement
from agentteams import proposals as P
from agentteams.atomicio import FileTooLargeError, read_regular_nofollow, write_new_atomic

QUEUE_DIR_REL = ".agentteams-queue"
REQUESTS_REL = ".agentteams-queue/requests"
#: R2: the ``agentteams_runner`` MCP server's own queue. The runner derives a request's channel from the directory
#: it arrived in, never from a field the sender writes.
MCP_REQUESTS_REL = ".agentteams-queue/mcp-requests"
ACKS_REL = ".agentteams-queue/acks"
RESULTS_REL = ".agentteams/queue-results"
CLAIMED_REL = ".agentteams/queue-claimed"
LOCK_REL = ".agentteams/runner.lock"
HEARTBEAT_REL = ".agentteams/runner.heartbeat"

#: Request kinds the runner serves. Commands and gates run confined (P4b).
KINDS = ("issue-dispatch", "apply-proposal", "run-request", "verify-ledger")
#: The kinds the MCP channel may queue. Everything else (dispatch, ledger checks, approvals) is the orchestrator's.
MCP_KINDS = ("apply-proposal", "run-request")
#: Request directory -> channel.
CHANNELS = {REQUESTS_REL: "orchestrator", MCP_REQUESTS_REL: "mcp"}
REQUEST_MAX_BYTES = 2 * 1024 * 1024
HEARTBEAT_STALE_SECONDS = 15
#: How often the heartbeat thread refreshes the heartbeat, independent of serving (R1): a long command no longer
#: makes the runner look dead to every other agent queueing behind it.
HEARTBEAT_INTERVAL_SECONDS = 3.0
#: Command cap for requests that arrive through the MCP channel (``"channel": "mcp"``). The queue is served one
#: request at a time, because each command's write check diffs the whole worktree before and after it runs; a
#: shorter cap bounds how long one agent's command holds everyone else's tool calls. A request that omits the field
#: gets the entry's own cap, which is never wider than the brief allows.
MCP_COMMAND_TIMEOUT = 120
#: The heartbeat thread stops beating when the serve loop has made no progress for this long, so a stuck main
#: thread can't hide behind a live heartbeat (@security R1 condition 2). Longer than any one request can take.
STALL_SECONDS = 7200 + 600
#: The largest result file the runner writes; bigger results are cut down to fit (R1 condition 4).
RESULT_MAX_BYTES = REQUEST_MAX_BYTES
RESULT_TTL_SECONDS = 300
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class RunnerError(P.ProposalError):
    """The runner can't serve, or the CLI found no runner answering."""


def _fit_result(result: dict[str, Any]) -> bytes:
    """Serialize *result* within :data:`RESULT_MAX_BYTES`, cutting command output and long lists if needed.

    JSON escaping can grow a byte of output to six, so capped streams alone may not fit (R1 condition 4). The cut
    is marked ``result_cut``; the ledger row holds the authoritative record either way.
    """
    data = json.dumps(result).encode()
    if len(data) <= RESULT_MAX_BYTES:
        return data
    cut = json.loads(data)
    inner = cut.get("result") if isinstance(cut.get("result"), dict) else {}
    for key in ("stdout", "stderr"):
        if isinstance(inner.get(key), str):
            inner[key] = inner[key][:16 * 1024]
    for key in ("undeclared_writes",):
        if isinstance(inner.get(key), list) and len(inner[key]) > 200:
            inner[key] = inner[key][:200] + [f"... and {len(inner[key]) - 200} more"]
    if isinstance(cut.get("error"), str):
        cut["error"] = cut["error"][:4000]
    inner["truncated"] = True
    cut["result_cut"] = True
    data = json.dumps(cut).encode()
    if len(data) <= RESULT_MAX_BYTES:
        return data
    return json.dumps({"id": result.get("id"), "kind": result.get("kind"), "ok": result.get("ok", False),
                       "result_cut": True, "error": "result too large to return; see the ledger"}).encode()


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
        for rel in (".agentteams", QUEUE_DIR_REL, REQUESTS_REL, MCP_REQUESTS_REL, ACKS_REL, RESULTS_REL, CLAIMED_REL):
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
        for rel in (REQUESTS_REL, MCP_REQUESTS_REL, ACKS_REL):
            (self.root / rel).mkdir(parents=True, exist_ok=True)  # session-writable by design
        for rel in (RESULTS_REL, CLAIMED_REL):
            (self.root / rel).mkdir(mode=0o700, parents=True, exist_ok=True)
        self._stop_heartbeat = threading.Event()
        self._progress = time.monotonic()
        #: The last heartbeat write failure (None when the last beat succeeded), for the serve loop to report.
        self.heartbeat_error: str | None = None
        self._heartbeat_thread: threading.Thread | None = None
        try:
            check_installed_readfs(self.root)
        except RunnerError:
            self.close()
            raise
        self._heartbeat()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name="agentteams-runner-heartbeat",
                                                  daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop_heartbeat.wait(HEARTBEAT_INTERVAL_SECONDS):
            if time.monotonic() - self._progress > STALL_SECONDS:
                self.heartbeat_error = "the serve loop made no progress; heartbeat stopped"
                continue  # stale heartbeat: clients see no runner, and the operator restarts it
            try:
                self._heartbeat()
                self.heartbeat_error = None
            except OSError as exc:  # a missed beat only makes clients wait; the next beat retries
                self.heartbeat_error = str(exc)

    def close(self) -> None:
        """Release the runner lock and remove the heartbeat (the lock file itself stays, on purpose).

        Raises:
            Nothing.
        """
        self._stop_heartbeat.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join()  # wakes at once on the event; only then is no beat in flight
        (self.root / HEARTBEAT_REL).unlink(missing_ok=True)
        os.close(self._lock_fd)

    def _heartbeat(self) -> None:
        _write_new(self.root / ".agentteams", Path(HEARTBEAT_REL).name, str(time.time()).encode())

    def _identity_args(self, request: dict[str, Any], channel: str) -> dict[str, Any]:
        """Channel rules for the proposal engine's identity check (R2).

        MCP channel: only :data:`MCP_KINDS`, and the nonce must resolve to the request's ``via_agent`` (the server
        instance's slug, fixed in the agent's canonical front matter). The check runs inside the engine's own
        attempt-counted lookup, and every nonce problem gives one uniform refusal, so the channel can't be used to
        test whether a nonce is live or whose it is. Orchestrator channel: agents in ``policy.mcp_agents`` are
        refused. ``via_agent`` is written by the sender: it stops an honest server replaying another agent's nonce,
        not a tampered server or a hand-written request file. Against those the defence is that no agent can read
        the queue (R1 Read denies, Goose readfs) and the server's launch integrity (R4).
        """
        if channel == "mcp":
            if request.get("kind") not in MCP_KINDS:
                raise RunnerError(f"the MCP channel can't queue {request.get('kind')!r}")
            via = request.get("via_agent")
            if not (isinstance(via, str) and via):
                raise RunnerError("an MCP-channel request must name its server instance's agent (via_agent)")
            return {"expect_agent": via}
        return {"refuse_agents": self.policy.mcp_agents}

    def _handle(self, request: dict[str, Any], channel: str = "orchestrator") -> dict[str, Any]:
        ident = self._identity_args(request, channel)
        kind = request.get("kind")
        if kind == "issue-dispatch":
            return {"nonce": P.issue_dispatch(self.root, str(request.get("agent") or ""))}
        if kind == "apply-proposal":
            return P.apply_proposal(request.get("artifact"), root=self.root, policy=self.policy,
                                    dry_run=bool(request.get("dry_run")), confine=True, **ident)
        if kind == "run-request":
            cap = MCP_COMMAND_TIMEOUT if channel == "mcp" else None
            return P.run_request(request.get("artifact"), root=self.root, policy=self.policy,
                                 dry_run=bool(request.get("dry_run")), confine=True, timeout_cap=cap, **ident)
        if kind == "verify-ledger":
            return {"problems": P.verify_ledger(self.root)}
        raise RunnerError("unknown request kind")

    def _open_session_dir(self, rel: str) -> int:
        """Open a session-writable queue dir without following a symlink anywhere in its last component."""
        try:
            return os.open(self.root / rel, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise RunnerError(f"{rel} is not a plain directory (a symlink?); refusing to serve") from exc

    def _serve_request(self, requests_fd: int, name: str, channel: str = "orchestrator") -> None:
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
            result["result"] = self._handle(request, channel)
            result["ok"] = True
        except P.UndeclaredWritesError as exc:
            result.update(error=str(exc), exit=3, result=exc.result)
        except P.ProposalError as exc:
            result.update(error=str(exc), exit=1)
        finally:
            claimed.unlink(missing_ok=True)
        result["serve_ms"] = int((time.monotonic() - served_from) * 1000)
        _write_new(self.root / RESULTS_REL, name, _fit_result(result))

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
        self._progress = time.monotonic()
        self._heartbeat()
        self._expire_results()
        served = 0
        for rel, channel in CHANNELS.items():
            requests_fd = self._open_session_dir(rel)
            try:
                for entry in sorted(os.scandir(requests_fd), key=lambda e: e.name):
                    if not (entry.name.endswith(".json") and _ID_RE.match(entry.name[: -len(".json")])):
                        continue  # temp files, stray names: ignored, never opened
                    if not entry.is_file(follow_symlinks=False):
                        os.unlink(entry.name, dir_fd=requests_fd)
                        continue
                    self._serve_request(requests_fd, entry.name, channel)
                    self._progress = time.monotonic()
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


def enqueue(root: Path, request: dict[str, Any], *, channel: str = "orchestrator") -> str:
    """Queue *request* for the runner and return its id.

    Args:
        root: The project root.
        request: ``{"kind": ..., ...}``. It never carries a root, brief or policy: the runner pins those.
        channel: ``"orchestrator"`` (the CLI) or ``"mcp"`` (the ``agentteams_runner`` server), which picks the
            queue directory; the runner reads the channel from the directory.

    Returns:
        The request id.

    Raises:
        RunnerError: When no runner is serving *root*.
    """
    if not runner_alive(root):
        raise RunnerError(f"no runner is serving this project (no fresh {HEARTBEAT_REL}); the operator starts one "
                          "outside any agent session: agentteams --serve-requests --project <root> "
                          "--description <brief>")
    rel = {v: k for k, v in CHANNELS.items()}.get(channel)
    if rel is None:
        raise RunnerError(f"unknown channel {channel!r}")
    request_id = secrets.token_hex(16)
    _write_new(root / rel, f"{request_id}.json", json.dumps(request).encode())
    return request_id


def poll_result(root: Path, request_id: str) -> dict[str, Any] | None:
    """Return the runner's result for *request_id* if it is ready (and acknowledge it), else None. Never blocks.

    Args:
        root: The project root.
        request_id: From :func:`enqueue`.

    Returns:
        The result, as :func:`wait_result` returns it, or None while the request is queued or running.

    Raises:
        RunnerError: On a bad id, or a runner that stopped answering while the request is still pending.
    """
    if not _ID_RE.match(request_id):
        raise RunnerError("bad request id")
    path = root / RESULTS_REL / f"{request_id}.json"
    if path.exists():
        result = json.loads(_read_regular(path, REQUEST_MAX_BYTES * 2))
        _write_new(root / ACKS_REL, request_id, b"")
        return result
    if not runner_alive(root):
        raise RunnerError("the runner stopped answering; restart it and re-queue the request")
    return None


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
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = poll_result(root, request_id)
        if result is not None:
            return result
        time.sleep(0.2)
    raise RunnerError(f"no result for {request_id} within {timeout:.0f}s; wait longer with --wait-result")
