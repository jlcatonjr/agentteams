"""Claude Code sandbox version tripwire (follow-up #6, 2026-09-30).

The Claude sandbox protections agentteams emits rely on Claude Code behaviour that is measured but not
documented: Claude Code binds every ``denyWrite`` entry read-only AFTER the read-write roots, and it
self-binds the parent of each ``denyWrite`` path, which is what makes an ancestor rename fail.
``tests/test_os_sandbox_product_enforcement.py`` measures this. When that whole module passes, it records
the Claude Code version on this host. ``--check-wiring`` compares the installed version with the
record and prints a NOTE when they differ.

ADVISORY ONLY: the record is user-writable and unauthenticated, so it never gates anything, and
``--check-wiring``'s exit status ignores it. Set ``AGENTTEAMS_NO_ITEST_TRIPWIRE=1`` to silence it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_ITEST_CMD = "RUN_CLAUDE_SANDBOX_ITEST=1 python -m pytest tests/test_os_sandbox_product_enforcement.py"
_FIELDS = ("claude_version", "passed_at", "platform", "agentteams_commit", "bwrap_version", "apparmor")


def record_path() -> Path:
    """Return the per-host record path (``$XDG_CACHE_HOME/agentteams/claude-sandbox-itest.json``)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "agentteams" / "claude-sandbox-itest.json"


def _clean(value: Any, limit: int = 80) -> str:
    """Return ``value`` as printable text: control characters dropped, truncated."""
    text = "".join(c for c in str(value) if c.isprintable())
    return text[:limit]


def read_record() -> dict[str, str] | None:
    """Return the sanitized record, or ``None`` when absent or malformed."""
    try:
        data = json.loads(record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: _clean(data.get(k, "")) for k in _FIELDS}


def write_record(fields: dict[str, Any]) -> Path:
    """Write the record atomically with mode 0600.

    Args:
        fields: Values for the record's known keys; unknown keys are dropped.

    Returns:
        The record path.

    Raises:
        OSError: The cache directory cannot be written.
    """
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({k: _clean(fields.get(k, ""), 200) for k in _FIELDS}, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".itest-", suffix=".json")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)  # no-op after a successful replace
    return path


def installed_claude_version(exe: str | None = None) -> str | None:
    """Return the first line of ``<exe> --version`` (``None`` when absent or failing).

    Args:
        exe: The binary to ask (the one the itest ran); default ``shutil.which("claude")``.
    """
    exe = exe or shutil.which("claude")
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (proc.stdout or "").strip().splitlines()
    return _clean(line[0]) if proc.returncode == 0 and line else None


def tripwire_notes() -> list[str]:
    """Return advisory NOTE lines comparing the installed Claude Code with the last recorded pass.

    Returns:
        Zero or more lines. Never raises; never affects an exit status.
    """
    try:
        return _tripwire_notes()
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:  # advisory only
        return [f"NOTE: sandbox itest tripwire skipped ({type(exc).__name__})."]


def _tripwire_notes() -> list[str]:
    if os.environ.get("AGENTTEAMS_NO_ITEST_TRIPWIRE") == "1":
        return []
    installed = installed_claude_version()
    if installed is None:
        return ["NOTE: `claude` was not found or did not report a version; the sandbox itest "
                "tripwire is skipped."]
    record = read_record()
    if record and record["claude_version"] == installed:
        return [f"NOTE: last recorded sandbox itest pass on this host (unauthenticated): "
                f"{installed} at {record['passed_at']}."]
    last = (f"last recorded pass was {record['claude_version']!r} at {record['passed_at']}"
            if record else "no recorded pass on this host")
    return [
        f"NOTE: installed Claude Code {installed!r} has no recorded sandbox itest pass ({last}). "
        "The emitted protections rely on measured, undocumented behaviour (denyWrite entries bound "
        "read-only after the rw roots; parent self-bind of each denyWrite path). Re-run: "
        f"{_ITEST_CMD}"
    ]
