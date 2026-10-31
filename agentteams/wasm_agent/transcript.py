"""transcript.py — the hash-chained run transcript of the WASM agent host (P05 §4.8, METAPLAN R4).

**Stage 0 is the only writer** (R4). :class:`FileJournal` creates ``<root>/.agentteams/wasm-runs/<run_id>/``
(0700, no symlink anywhere on the path) and ``transcript.jsonl`` (0600, ``O_CREAT|O_EXCL|O_NOFOLLOW|O_APPEND``),
and appends one record per call: it validates the record kind against the closed set, assigns ``seq``, ``ts``,
``run_id``, ``prev`` and ``hash``, withholds any record that would carry a known secret (S-3), ``fsync``s and only
then returns, which is the ack journal-before-call depends on. The confined host (Stage 1) sends record *bodies* over
the control pipe (:class:`PipeJournal`) and never sees or chooses ``seq``/``prev``/``hash``, so it cannot fork the
chain.

``hash = sha256(prev || canonical_json(record without hash))``. This is a hash chain, not a MAC: it detects edits by
anything that does not rewrite the whole chain; the head is printed to the operator at run end. The reserved
provider keys (``_provider_id`` / ``_provider_sig``) are never journaled; a ``model-reply`` record carries their
digests and an HMAC under the run's key (:func:`reserved_mac`), which Stage 0 checks before appending. Stage 1 holds
that key (it runs the vault), so the MAC catches guest- or model-side tampering and mis-binding, not a compromised
Stage 1.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import os
import stat
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agentteams.atomicio import write_new_atomic
from agentteams.wasm_agent.protocol import RUN_ID_RE, ProtocolError, canonical, strict_loads

#: Closed record-kind set.
RECORD_KINDS = ("run-start", "model-request", "model-reply", "dlp", "guest-step", "host-tool", "delegate-start",
                "delegate-end", "denied", "revoked", "notice", "protocol-error", "withheld", "run-end")
#: Keys only the writer assigns.
WRITER_KEYS = frozenset({"seq", "ts", "run_id", "kind", "prev", "hash"})
#: Largest record body accepted (canonical JSON bytes).
MAX_RECORD_BYTES = 4 << 20
#: ``prev`` of the first record.
GENESIS = "0" * 64
RUNS_REL = (".agentteams", "wasm-runs")
TRANSCRIPT_NAME = "transcript.jsonl"
RESULT_NAME = "result.json"
REVOKED_NAME = "REVOKED"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_RESERVED_FIELDS = ("turn", "index", "type", "id", "pid_sha256", "sig_sha256")


class JournalError(Exception):
    """The transcript could not be written (or the ack never came): no further model call may be made."""


class JournalSink(Protocol):
    """Where the host loop sends records: :class:`FileJournal` (Stage 0) or :class:`PipeJournal` (Stage 1)."""

    def append(self, kind: str, body: Mapping[str, Any]) -> str:
        """Append one record and return its hash once it is durable."""


def contains_secret(text: str, secrets: Iterable[str]) -> bool:
    """Whether *text* contains any known secret (exact match; secrets shorter than 8 characters are ignored).

    Args:
        text: The text to check.
        secrets: The known secrets.

    Returns:
        True on a hit.

    Raises:
        Nothing.
    """
    return any(len(s) >= 8 and s in text for s in secrets)


def reserved_mac(key: bytes, entry: Mapping[str, Any]) -> str:
    """HMAC-SHA256 binding one reply block's reserved-key digests to its place in the conversation.

    Args:
        key: The run's MAC key (Stage 0 generates it; it never reaches a guest or the transcript).
        entry: ``turn``, ``index``, ``type``, ``id``, ``pid_sha256``, ``sig_sha256``.

    Returns:
        The hex MAC.

    Raises:
        KeyError: A field is missing.
    """
    return hmac.new(key, canonical([entry[f] for f in _RESERVED_FIELDS]), hashlib.sha256).hexdigest()


def check_reserved(key: bytes | None, body: Mapping[str, Any]) -> None:
    """Verify the reserved-key MACs a ``model-reply`` record carries.

    Args:
        key: The run's MAC key, or None to skip (in-process tests without one).
        body: The record body.

    Returns:
        None.

    Raises:
        JournalError: A malformed entry or a MAC mismatch.
    """
    entries = body.get("reserved", [])
    if not isinstance(entries, list):
        raise JournalError("model-reply reserved must be a list")
    for e in entries:
        if not isinstance(e, dict) or set(e) != {*_RESERVED_FIELDS, "mac"}:
            raise JournalError("model-reply reserved entry has the wrong shape")
        if key is not None and not hmac.compare_digest(str(e["mac"]), reserved_mac(key, e)):
            raise JournalError("model-reply reserved-key MAC mismatch")


def ensure_dir(root: str, parts: Iterable[str]) -> str:
    """Create (0700) or check each directory of ``root/parts``; refuse a symlink or a non-directory anywhere.

    Stage 0 only (R4): the confined host never calls this.

    Args:
        root: An existing real directory.
        parts: Path components below it.

    Returns:
        The final path.

    Raises:
        JournalError: A component is a symlink or not a directory.
        OSError: A directory cannot be created.
    """
    path = root
    for part in parts:
        path = os.path.join(path, part)
        if not os.path.lexists(path):
            with contextlib.suppress(FileExistsError):   # a concurrent run created it first; lstat checks it
                os.mkdir(path, 0o700)
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode):
            raise JournalError(f"{path} is not a real directory (symlink?); refused")
    return path


def run_dir_path(project_root: str | Path, run_id: str) -> Path:
    """The run directory of *run_id* (not created).

    Args:
        project_root: The project root.
        run_id: 32 lowercase hex.

    Returns:
        ``<root>/.agentteams/wasm-runs/<run_id>``.

    Raises:
        ValueError: A malformed run id.
    """
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise ValueError("run_id must be 32 lowercase hex")
    return Path(os.path.realpath(str(project_root))).joinpath(*RUNS_REL, run_id)


def revoke_run(project_root: str | Path, run_id: str, reason: str) -> Path:
    """Revoke a run: create its ``REVOKED`` sentinel (an operator CLI action; P08 exposes it).

    Args:
        project_root: The project root.
        run_id: The run.
        reason: Recorded in the sentinel (plain text, at most 200 characters).

    Returns:
        The sentinel path.

    Raises:
        FileNotFoundError: No such run directory.
        FileExistsError: Already revoked.
        ValueError: A malformed run id.
    """
    run_dir = run_dir_path(project_root, run_id)
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise FileNotFoundError(f"no wasm run {run_id}")
    path = run_dir / REVOKED_NAME
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, reason[:200].encode("utf-8", "replace") + b"\n")
    finally:
        os.close(fd)
    return path


class FileJournal:
    """Stage 0's transcript writer (the only one; see the module docstring)."""

    def __init__(self, project_root: str | Path, run_id: str, *, secrets: Iterable[str] = (),
                 mac_key: bytes | None = None, clock: Any = time.time) -> None:
        """Create the run directory and an empty transcript.

        Args:
            project_root: The project root.
            run_id: 32 lowercase hex (fresh per run).
            secrets: Known secrets no record may carry (the provider key, the secrets files' values).
            mac_key: The run's reserved-key MAC key (checked on ``model-reply`` records).
            clock: Wall clock for ``ts``.

        Raises:
            JournalError: The directory or file cannot be created safely (exists, symlinked, unwritable).
        """
        self.run_id = run_id
        root = os.path.realpath(str(project_root))
        try:
            run_dir_path(root, run_id)
            ensure_dir(root, RUNS_REL)
            self.run_dir = Path(os.path.join(root, *RUNS_REL, run_id))
            os.mkdir(self.run_dir, 0o700)
            self._fd = os.open(self.run_dir / TRANSCRIPT_NAME,
                               os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | _NOFOLLOW, 0o600)
        except (OSError, ValueError) as exc:
            raise JournalError(f"cannot create the run transcript: {exc}") from exc
        self._secrets = tuple(s for s in secrets if len(s) >= 8)
        self._mac_key = mac_key
        self._clock = clock
        self.seq = 0
        self.head = GENESIS
        self.ended = False
        self.withheld = 0

    @property
    def path(self) -> Path:
        """The transcript file.

        Returns:
            ``<run_dir>/transcript.jsonl``.
        """
        return self.run_dir / TRANSCRIPT_NAME

    def append(self, kind: str, body: Mapping[str, Any]) -> str:
        """Validate, chain, secret-check, write and fsync one record.

        Args:
            kind: One of :data:`RECORD_KINDS`.
            body: The record body (no writer keys).

        Returns:
            The new record's hash (the ack).

        Raises:
            JournalError: An unknown kind, writer keys in the body, an oversize body, a reserved-key MAC mismatch,
                a record after ``run-end``, or a write failure.
        """
        if self.ended:
            raise JournalError("the transcript is closed")
        if kind not in RECORD_KINDS or not isinstance(body, Mapping):
            raise JournalError(f"unknown record kind {str(kind)[:40]!r}")
        if WRITER_KEYS & set(body):
            raise JournalError("a record body may not carry seq/ts/run_id/kind/prev/hash")
        try:
            raw = canonical(dict(body))
        except (TypeError, ValueError) as exc:
            raise JournalError(f"record body is not JSON: {exc}") from exc
        if len(raw) > MAX_RECORD_BYTES:
            raise JournalError(f"record body is {len(raw)} bytes; the cap is {MAX_RECORD_BYTES}")
        if kind == "model-reply":
            check_reserved(self._mac_key, body)
        if contains_secret(raw.decode("ascii"), self._secrets) or contains_secret(
                raw.decode("unicode_escape", "replace"), self._secrets):
            self.withheld += 1
            kind, body = "withheld", {"of": kind, "reason": "known-secret"}
        record = {"seq": self.seq, "ts": round(self._clock(), 3), "run_id": self.run_id, "kind": kind, **dict(body),
                  "prev": self.head}
        record["hash"] = hashlib.sha256(self.head.encode() + canonical(record)).hexdigest()
        line = canonical(record) + b"\n"
        try:
            view = memoryview(line)
            while view:
                view = view[os.write(self._fd, view):]
            os.fsync(self._fd)
        except OSError as exc:
            raise JournalError(f"transcript write failed: {exc}") from exc
        self.seq += 1
        self.head = record["hash"]
        if kind == "run-end":
            self.ended = True
        return self.head

    def close(self) -> None:
        """Write ``run-end{failed, host-exited}`` if the host never ended the run, then close the file.

        Returns:
            None.

        Raises:
            Nothing (a final write failure leaves the transcript ``incomplete``, which :func:`verify_transcript`
            reports).
        """
        if not self.ended and self._fd >= 0:
            try:
                self.append("run-end", {"status": "failed", "reason": "host-exited"})
            except JournalError as exc:
                print(f"WASM AGENT: transcript left incomplete ({exc})", file=sys.stderr)
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def write_result(self, result: Mapping[str, Any]) -> Path:
        """Write ``result.json`` (0600, atomic) beside the transcript.

        Args:
            result: The final result object.

        Returns:
            Its path.

        Raises:
            JournalError: The write failed, or the result carries a known secret.
        """
        data = canonical(dict(result))
        if contains_secret(data.decode("ascii"), self._secrets):
            raise JournalError("the run result carries a known secret; withheld")
        try:
            return write_new_atomic(self.run_dir, RESULT_NAME, data + b"\n", mode=0o600)
        except OSError as exc:
            raise JournalError(f"result write failed: {exc}") from exc


class PipeJournal:
    """Stage 1's journal: sends record bodies to Stage 0 and waits for the ack."""

    def __init__(self, control: Any) -> None:
        """Bind to the control client.

        Args:
            control: An object with ``call(message) -> reply`` (``stage1.ControlClient``).

        Raises:
            Nothing.
        """
        self._control = control

    def append(self, kind: str, body: Mapping[str, Any]) -> str:
        """Send one record and wait for Stage 0's ack.

        Args:
            kind: The record kind.
            body: The record body.

        Returns:
            The record hash Stage 0 assigned.

        Raises:
            JournalError: No ack, a refusal, or a closed pipe.
        """
        try:
            reply = self._control.call({"op": "journal", "kind": kind, "body": dict(body)})
        except (OSError, ProtocolError) as exc:
            raise JournalError(f"no journal ack: {exc}") from exc
        if not reply.get("ok") or not isinstance(reply.get("hash"), str):
            raise JournalError(f"journal refused: {str(reply.get('error', ''))[:200]}")
        return reply["hash"]


@dataclass(frozen=True)
class TranscriptReport:
    """What :func:`verify_transcript` found."""

    ok: bool
    complete: bool
    records: int
    head: str
    status: str | None
    error: str | None


def verify_transcript(path: str | Path) -> TranscriptReport:
    """Re-walk a transcript's hash chain.

    Args:
        path: ``transcript.jsonl``.

    Returns:
        A :class:`TranscriptReport`: ``ok`` when every record parses, chains and is a known kind; ``complete`` when
        the last record is ``run-end`` (a crash leaves it ``incomplete``).

    Raises:
        Nothing.
    """
    prev, n, status, run_id = GENESIS, 0, None, None
    try:
        with open(path, "rb") as fh:
            lines = fh.read().split(b"\n")
    except OSError as exc:
        return TranscriptReport(False, False, 0, prev, None, f"unreadable: {exc}")
    if lines and lines[-1] == b"":
        lines.pop()
    last_kind = None
    for line in lines:
        try:
            rec = strict_loads(line)
        except ProtocolError as exc:
            return TranscriptReport(False, False, n, prev, status, f"record {n}: {exc}")
        if not isinstance(rec, dict) or not {"seq", "ts", "run_id", "kind", "prev", "hash"} <= set(rec):
            return TranscriptReport(False, False, n, prev, status, f"record {n}: missing writer keys")
        if rec["seq"] != n or rec["prev"] != prev or rec["kind"] not in RECORD_KINDS:
            return TranscriptReport(False, False, n, prev, status, f"record {n}: seq/prev/kind out of chain")
        first_ok = rec["kind"] in ("run-start", "protocol-error")   # a host that broke before run-start
        if (n == 0 and not first_ok) or (n > 0 and rec["kind"] == "run-start") or \
                (run_id is not None and rec["run_id"] != run_id):
            return TranscriptReport(False, False, n, prev, status, f"record {n}: run-start/run_id mismatch")
        if last_kind == "run-end":
            return TranscriptReport(False, False, n, prev, status, f"record {n}: after run-end")
        body = {k: v for k, v in rec.items() if k != "hash"}
        want = hashlib.sha256(prev.encode() + canonical(body)).hexdigest()
        if not hmac.compare_digest(want, str(rec["hash"])):
            return TranscriptReport(False, False, n, prev, status, f"record {n}: hash mismatch")
        prev, run_id, last_kind = rec["hash"], rec["run_id"], rec["kind"]
        if rec["kind"] == "run-end":
            status = rec.get("status")
        n += 1
    complete = last_kind == "run-end"
    return TranscriptReport(n > 0, complete, n, prev, status, None if n else "empty transcript")
