"""Tell a derived copy of agentteams' sandbox launcher apart: ``current``, ``older``, ``modified`` or ``missing``.

Teams carry a copy of ``agentteams/templates/universal/sandbox/confine-run.sh`` as ``sandbox/confine-run.sh``. The
launcher carries no version stamp, and it cannot get one: consumers pin its sha256, so writing a stamp into it would
break every pin. Its version is told by its bytes instead. ``agentteams/data/launcher_lineage.json`` lists the
sha256 of every released version of the template (oldest first, generated from git history by
``scripts/build_launcher_lineage.py``, never edited by hand), each marked ``security`` when it closed a gap.

States (:data:`STATES`):

* ``current`` — the bytes are the newest released version.
* ``older`` — the bytes are an earlier released version; :attr:`LauncherState.newer` counts the later versions and
  :attr:`LauncherState.newer_security` says whether any of them is a security fix.
* ``modified`` — the bytes match no released version: a local edit or a fork. This is the dangerous case and is never
  folded into a benign label. A symlink, a non-regular file and a file larger than :data:`MAX_LAUNCHER_BYTES` are
  ``modified`` too (refused, not followed or read whole).
* ``missing`` — there is no file.

This is informational: the launcher's own fail-closed checks remain the gate.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).resolve().parent
#: The shipped lineage (see the module docstring).
LINEAGE_PATH = _PACKAGE_DIR / "data" / "launcher_lineage.json"
#: The launcher template whose versions the lineage lists.
TEMPLATE_PATH = _PACKAGE_DIR / "templates" / "universal" / "sandbox" / "confine-run.sh"
#: A launcher larger than this is not read (it is ``modified``: no released version is near this size).
MAX_LAUNCHER_BYTES = 1_000_000
STATES = ("current", "older", "modified", "missing")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class LauncherState:
    """What a launcher copy is, relative to the released versions.

    Attributes:
        state: One of :data:`STATES`.
        detail: One line for a person (what was found, and for ``older`` the newest version's commit).
        newer: For ``older``, how many later released versions there are; else 0.
        newer_security: For ``older``, whether any later version is a security fix; else False.
    """

    state: str
    detail: str
    newer: int = 0
    newer_security: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the state as plain data.

        Returns:
            ``{"state", "detail", "newer", "newer_security"}``.

        Raises:
            Nothing.
        """
        return asdict(self)


def load_lineage(path: Path = LINEAGE_PATH) -> list[dict[str, Any]]:
    """Read and check the lineage file.

    Args:
        path: The lineage JSON (default: the shipped one).

    Returns:
        The entries, oldest first; each has ``sha256`` (str), ``commit`` (str), ``date`` (str) and
        ``security`` (bool).

    Raises:
        OSError: When the file cannot be read.
        ValueError: When it is not a lineage (bad JSON, no entries, or a malformed entry).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("versions") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: no 'versions' list")
    for n, e in enumerate(entries):
        if not (isinstance(e, dict) and isinstance(e.get("sha256"), str) and _SHA256.match(e["sha256"])
                and isinstance(e.get("commit"), str) and isinstance(e.get("date"), str)
                and isinstance(e.get("security"), bool)):
            raise ValueError(f"{path}: version {n} is malformed")
    return entries


def _read_bounded(path: Path) -> tuple[bytes | None, str]:
    """Return (bytes, "") for a regular file, (None, "") when absent, or (None, why) when refused."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (FileNotFoundError, NotADirectoryError):
        return None, ""
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None, f"{path} is a symlink (refused, not followed)"
        return None, f"{path} is unreadable ({exc.strerror})"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, f"{path} is not a regular file"
        data = os.read(fd, MAX_LAUNCHER_BYTES + 1)
    except OSError as exc:
        return None, f"{path} is unreadable ({exc.strerror})"
    finally:
        os.close(fd)
    if len(data) > MAX_LAUNCHER_BYTES:
        return None, f"{path} is larger than {MAX_LAUNCHER_BYTES} bytes (no released version is)"
    return data, ""


def launcher_state(path: Path, lineage: list[dict[str, Any]] | None = None) -> LauncherState:
    """Classify the launcher copy at *path* against the released versions.

    Args:
        path: A launcher copy (typically ``<project>/sandbox/confine-run.sh``). Read with ``O_NOFOLLOW``, at most
            :data:`MAX_LAUNCHER_BYTES`.
        lineage: Entries from :func:`load_lineage` (default: the shipped lineage).

    Returns:
        The state. A symlink, a non-regular or unreadable file, an oversized file and bytes that match no released
        version are ``modified``; an absent file is ``missing``.

    Raises:
        OSError: When the shipped lineage cannot be read (only when *lineage* is None).
        ValueError: When the shipped lineage is malformed (only when *lineage* is None).
    """
    versions = load_lineage() if lineage is None else lineage
    data, refused = _read_bounded(path)
    if refused:
        return LauncherState("modified", refused)
    if data is None:
        return LauncherState("missing", f"no launcher at {path}")
    digest = hashlib.sha256(data).hexdigest()
    hits = [i for i, e in enumerate(versions) if e["sha256"] == digest]
    if not hits:
        return LauncherState("modified", f"sha256 {digest[:12]} matches no released version (a local edit or a fork)")
    at = versions[hits[-1]]
    later = [e for e in versions[hits[-1] + 1:] if e["sha256"] != digest]
    if not later:
        return LauncherState("current", f"released version {at['commit'][:8]} ({at['date'][:10]})")
    newer = len({e["sha256"] for e in later})
    security = any(e["security"] for e in later)
    newest = versions[-1]
    return LauncherState("older", (f"released version {at['commit'][:8]} ({at['date'][:10]}); {newer} newer "
                                   f"version(s){', including a security fix' if security else ''}; newest "
                                   f"{newest['commit'][:8]} ({newest['date'][:10]})"),
                         newer=newer, newer_security=security)
