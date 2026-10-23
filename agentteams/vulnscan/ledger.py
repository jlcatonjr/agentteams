"""The executor's append-only, MAC-chained action ledger.

Every remediation action leaves rows here:

- ``begun`` before execution, which is the recorded clearance and use reservation (C-5);
- then ``done``, ``failed`` or ``refused``;
- ``ack`` when the operator acknowledges a review.

Each row carries the previous row's MAC and its own HMAC-SHA256 under a local key in
``<config>/keys/vulnscan-ledger.key``. The sandbox masks that directory (``confine-run.sh``
masks ``~/.config/agentteams/keys``), so a confined agent cannot rebuild the chain or forge
rows that the review would trust. A writer with the key can still rewrite history: this is
tamper *evidence* against the sandboxed agent, not against the operator's own account.

All writers serialise on an exclusive ``flock``. That covers the executor's whole
verify → reserve → execute → record sequence (see :func:`locked`).

Tail truncation would otherwise pass verification: the remaining chain is still valid, but the
dropped rows, an escalation say, are gone. So every append also raises a **high-water mark**
(``<config>/keys/vulnscan-ledger.hwm``). A ledger whose last ``seq`` is below the mark fails to read.
That includes a deleted ledger.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan import paths

GENESIS = "0" * 64


def ledger_path() -> Path:
    """Return the ledger file path.

    Returns:
        ``<state>/ledger.jsonl``.
    """
    return paths.state_dir() / "ledger.jsonl"


def _key() -> bytes:
    kdir = paths.config_dir() / "keys"
    kfile = kdir / "vulnscan-ledger.key"
    if not kfile.exists():
        kdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(kfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(32))
    st = kfile.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise PermissionError(f"{kfile} must be owned by you with mode 600")
    return bytes.fromhex(kfile.read_text().strip())


def hwm_path() -> Path:
    """Return the high-water-mark file (beside the ledger key, so the sandbox cannot reach it).

    Returns:
        ``<config>/keys/vulnscan-ledger.hwm``.
    """
    return paths.config_dir() / "keys" / "vulnscan-ledger.hwm"


def _high_water() -> int:
    p = hwm_path()
    if not p.exists():
        return 0
    text = p.read_text(encoding="utf-8").strip()
    if not text.isdigit():
        raise ValueError(f"{p} is corrupt — the ledger cannot be trusted")
    return int(text)


def _raise_high_water(seq: int) -> None:
    p = hwm_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(str(seq))
    tmp.replace(p)


def _mac(key: bytes, prev: str, row: dict[str, Any]) -> str:
    body = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hmac.new(key, (prev + body).encode(), hashlib.sha256).hexdigest()


@contextlib.contextmanager
def locked() -> Iterator[None]:
    """Hold the ledger's exclusive lock (blocking).

    Yields:
        Nothing; the lock is released on exit.
    """
    ledger_path().parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path().with_suffix(".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read(*, verify: bool = True) -> list[dict[str, Any]]:
    """Return all rows, verifying the MAC chain.

    Args:
        verify: Check every MAC and the chain linkage.

    Returns:
        The rows in order.

    Raises:
        ValueError: The chain is broken, a MAC does not verify, or rows are missing below the
            high-water mark (tampering, truncation or corruption).
    """
    path = ledger_path()
    mark = _high_water() if verify else 0
    if not path.exists():
        if mark:
            raise ValueError(f"the ledger is missing but {mark} rows were recorded — the ledger was removed")
        return []
    key = _key() if verify else b""
    rows: list[dict[str, Any]] = []
    prev = GENESIS
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        row = json.loads(line)
        if verify:
            mac = row.get("mac", "")
            body = {k: v for k, v in row.items() if k != "mac"}
            if body.get("prev") != prev or not hmac.compare_digest(mac, _mac(key, prev, body)):
                raise ValueError(f"ledger row {n} fails verification — the ledger was altered")
            prev = mac
        rows.append(row)
    if verify and (rows[-1]["seq"] if rows else 0) < mark:
        raise ValueError(f"the ledger ends at row {rows[-1]['seq'] if rows else 0} but {mark} rows were recorded "
                         "— rows were removed from its end")
    return rows


def append(kind: str, **fields: Any) -> dict[str, Any]:
    """Append one row. Call inside :func:`locked`.

    Args:
        kind: begun | done | failed | refused | ack | note.
        **fields: grant_id, op, target, request_id, detail, … (JSON-serialisable).

    Returns:
        The written row.
    """
    rows = read()
    prev = rows[-1]["mac"] if rows else GENESIS
    body = {"seq": len(rows) + 1, "ts": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "kind": kind, **fields, "prev": prev}
    body["mac"] = _mac(_key(), prev, {k: v for k, v in body.items() if k != "mac"})
    fd = os.open(ledger_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(body, sort_keys=True) + "\n")
    _raise_high_water(body["seq"])
    return body


def uses(grant_id: str, rows: list[dict[str, Any]] | None = None) -> int:
    """Count actions charged to a grant (every ``begun`` row).

    Args:
        grant_id: Grant id.
        rows: Pre-read rows (optional).

    Returns:
        Number of reserved uses.
    """
    return sum(1 for r in (rows if rows is not None else read()) if r.get("kind") == "begun" and r.get("grant_id") == grant_id)


def consumed_request_ids(rows: list[dict[str, Any]] | None = None) -> set[str]:
    """Return request ids already processed (replay protection).

    Args:
        rows: Pre-read rows (optional).

    Returns:
        Ids seen in any row.
    """
    return {str(r["request_id"]) for r in (rows if rows is not None else read()) if r.get("request_id")}
