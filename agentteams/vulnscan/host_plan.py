"""The plan digest the host-changing ops confirm (``server-apply``, ``runtime-patch``), and its payload key.

One definition for :mod:`.server_apply`, :mod:`.runtime_patch` and :mod:`.host_change`. :func:`plan_sha256` is the
Python twin of the server kit's ``plan_digest`` shell function (``run-host.sh``, ``L2-os-patch.sh``,
``L4-python-app.sh``, ``L6-py-venv.sh``, ``L7-remove-path.sh``); a test keeps them equal. Leaf module: standard
library only.
"""

from __future__ import annotations

import hashlib
import re

#: The payload key ``vulnscan act`` passes the confirmed digest under.
CONFIRM_KEY = "confirm_sha256"
#: A sha256 hex digest.
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def plan_sha256(text: str) -> str:
    """Return the sha256 of the normalised plan in ``text``.

    PLAN lines only (``PLAN<TAB>...``), CR dropped, whitespace runs squeezed to one space, trailing space dropped,
    sorted bytewise and de-duplicated, each newline-terminated.

    Args:
        text: Kit or dry-run output.

    Returns:
        Hex sha256 (of the empty string when there is no PLAN line).
    """
    lines = {re.sub(r"[ \t\v\f]+", " ", ln).rstrip(" ")
             for ln in text.replace("\r", "").split("\n") if ln.startswith("PLAN\t")}
    body = "".join(ln + "\n" for ln in sorted(lines, key=lambda s: s.encode()))
    return hashlib.sha256(body.encode()).hexdigest()


def plan_lines(text: str) -> list[str]:
    """Return the PLAN lines of ``text`` (for display), in order.

    Args:
        text: Kit or dry-run output.

    Returns:
        The lines.
    """
    return [ln for ln in text.replace("\r", "").split("\n") if ln.startswith("PLAN\t")]
