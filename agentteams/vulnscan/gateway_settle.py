"""Settling a gateway call still in flight when the gateway stops: shared by the gateway and the launcher.

The gateway reserves each call's worst case before relaying it (:meth:`.gateway.Meter.reserve`) and releases the
reservation when the reply is metered. A call still reserved when the gateway stops was billed upstream but never
metered, so it is charged its reservation instead (fail closed): by the gateway on SIGTERM
(:meth:`.gateway.Meter.shutdown`), or, when the gateway was killed outright, by the launcher from the
``spend.json`` it left (:func:`settle_spend_file`). Host-side code imports this module, never the gateway.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .gateway_dialects import number

#: The largest ``spend.json`` the launcher reads (the gateway writes about 1 KB).
MAX_SPEND_BYTES = 1 << 20

def charge_reservation(totals: Any, inflight: int) -> int:
    """Charge ``inflight`` reserved calls their reservation on ``totals`` (a :class:`.gateway.Meter` or a namespace).

    Shared by :meth:`.gateway.Meter.settle_inflight` and :func:`settle_spend_file`, so the gateway and the launcher settle
    the same way. A reservation left without a call count (``inflight`` 0) is still charged as one call.

    Returns:
        The number of calls charged.
    """
    if not (inflight or totals.reserved_usd or totals.reserved_tokens):
        return 0
    calls = max(inflight, 1)
    totals.calls += calls
    totals.settled_inflight += calls
    if not totals.reserved_usd:   # no price for the reservation: its spend is unknown, so it counts as unmetered
        totals.unmetered += calls
    totals.spent += totals.reserved_usd
    totals.completion_tokens += totals.reserved_tokens
    totals.reserved_usd, totals.reserved_tokens, totals.inflight = 0.0, 0, 0
    return calls


def settle_spend_file(path: Path) -> int:
    """Settle a ``spend.json`` a gateway left with a reservation (it was killed mid-call): the launcher's side.

    The gateway serialises calls, so at most one call is reserved at a time; it is charged as
    :meth:`.gateway.Meter.settle_inflight` would. The file is rewritten atomically; nothing is written when no
    reservation is left. An absent or unreadable file is left alone (the launcher reports it, G12). The directory is
    one the gateway container can write, so nothing here follows a symlink: ``spend.json`` is opened with
    ``O_NOFOLLOW`` and must be a regular file of at most :data:`MAX_SPEND_BYTES`, and the replacement is written
    to a fresh ``O_EXCL`` temp file (:func:`write_atomic`).

    Args:
        path: ``<run>/gateway/spend.json``.

    Returns:
        The number of calls settled (0 or 1).

    Raises:
        OSError: The settled file cannot be written.
    """
    try:
        data = json.loads(_read_regular(path))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    num = {k: number(data.get(k)) or 0 for k in ("reserved_usd", "reserved_tokens", "spent_usd", "calls",
                                                  "unmetered", "completion_tokens", "settled_inflight")}
    totals = argparse.Namespace(reserved_usd=float(num["reserved_usd"]), reserved_tokens=int(num["reserved_tokens"]),
                                spent=float(num["spent_usd"]), calls=int(num["calls"]),
                                unmetered=int(num["unmetered"]), completion_tokens=int(num["completion_tokens"]),
                                settled_inflight=int(num["settled_inflight"]), inflight=0)
    settled = charge_reservation(totals, 0)
    if not settled:
        return 0
    data.update(spent_usd=round(totals.spent, 6), calls=totals.calls, unmetered=totals.unmetered,
                completion_tokens=totals.completion_tokens, reserved_usd=0.0, reserved_tokens=0,
                settled_inflight=totals.settled_inflight, settled_by="launcher",
                updated=datetime.now(UTC).isoformat())
    write_atomic(path, json.dumps(data, indent=2))
    return settled


def _read_regular(path: Path) -> str:
    """Return a regular file's text without following a symlink (ValueError: not a regular file, or too large)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("spend.json is not a regular file")
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            raw = fh.read(MAX_SPEND_BYTES + 1)
    finally:
        if fd != -1:
            os.close(fd)
    if len(raw) > MAX_SPEND_BYTES:
        raise ValueError("spend.json is too large")
    return raw.decode("utf-8")


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a fresh temp file beside it and ``os.replace`` (G12).

    The temp file is created exclusively under a random name (``mkstemp``: ``O_CREAT | O_EXCL``), so a symlink
    planted beside ``path`` is never written through; ``os.replace`` swaps a symlinked ``path`` itself, not its
    target. A reader never sees a truncated file; on failure the temp is removed, the previous file stays and the
    error propagates.
    """
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp, replaced = Path(name), False
    try:
        os.fchmod(fd, 0o644)   # as before: the launcher and the operator read it
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        replaced = True
    finally:
        if not replaced:   # the error propagates; only the temp is removed
            tmp.unlink(missing_ok=True)
