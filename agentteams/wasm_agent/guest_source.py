"""guest_source.py — the pinned agent-loop guest program (WASM P07) and its verify-on-load.

The program that runs inside every agent's WASM guest is package data, ``agentteams/data/wasm_agent_guest.py``. It is
never imported by agentteams: Stage 0 reads its bytes with :func:`load_guest_source`, which re-hashes them against
:data:`GUEST_SHA256` on every load, and hands them to ``broker.StepPreparer`` (which checks the hash again per step).
A mismatch refuses the run; nothing falls back to an unpinned program.

The file is also listed in ``agentteams/integrity.py``, so ``--verify-integrity`` reports an edit even when nothing
loads it. Stdlib only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: The guest program, relative to the ``agentteams`` package directory.
GUEST_REL = "data/wasm_agent_guest.py"
#: sha256 of the guest program's bytes (re-pinned whenever the program changes; a test checks it).
GUEST_SHA256 = "fadd6d43c7d6d45d848d9c46add4e2cd4abe1f2e8dc7b79bc62565afc14de18e"
#: Largest accepted guest program (the source is reviewed whole; a bigger file is not the reviewed one).
MAX_GUEST_BYTES = 256 << 10


class GuestSourceError(RuntimeError):
    """The guest program is missing, oversize, or does not match :data:`GUEST_SHA256`."""


def guest_path() -> Path:
    """Where the guest program ships.

    Returns:
        The absolute path of ``agentteams/data/wasm_agent_guest.py``.

    Raises:
        Nothing.
    """
    return Path(__file__).resolve().parents[1] / GUEST_REL


def load_guest_source() -> tuple[bytes, str]:
    """Read the guest program and verify it against its pin (every call re-reads and re-hashes).

    Returns:
        ``(source bytes, sha256)``, ready for ``stage0(guest_source=, guest_sha256=)``.

    Raises:
        GuestSourceError: The file is missing, unreadable, oversize or its sha256 differs from the pin.
    """
    path = guest_path()
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_GUEST_BYTES + 1)
    except OSError as exc:
        raise GuestSourceError(f"cannot read the WASM agent guest program: {exc.strerror}") from exc
    if len(data) > MAX_GUEST_BYTES:
        raise GuestSourceError(f"the WASM agent guest program exceeds {MAX_GUEST_BYTES} bytes")
    digest = hashlib.sha256(data).hexdigest()
    if digest != GUEST_SHA256:
        raise GuestSourceError(f"the WASM agent guest program does not match its pin (sha256 {digest[:16]}..., "
                               f"pinned {GUEST_SHA256[:16]}...); reinstall agentteams")
    return data, digest
