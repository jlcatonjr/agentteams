"""Per-stage day markers and the exit-2 status file (workflow-conformance test, Phase A, request item 6).

The hourly sweep-plus-judgment job re-bought its sweep every hour after the judgment step refused. This module is
the fix's mechanism: **one marker per stage per day, written on every exit code, including 2**. A stage that already
has today's marker is not run again; its recorded exit code is returned instead, so a refusal is never paid for twice
(AC5's no-repay test). The launchd job that calls it is Phase B.

A VOID (exit 2) is also written to a **status file** at a path IM can read (AC4):
``~/.local/state/agentteams/workflow-conformance/status.json`` by default. Phase B adds the macOS notification.

stdlib only; every write is a temp file plus ``os.replace``.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

#: Default state directory (markers and status file), outside every work tree.
DEFAULT_STATE_DIR = Path("~/.local/state/agentteams/workflow-conformance")
STATUS_NAME = "status.json"
_STAGE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def default_status_path() -> Path:
    """Return the default status-file path (AC4), with ``~`` expanded.

    Returns:
        ``~/.local/state/agentteams/workflow-conformance/status.json``.
    """
    return DEFAULT_STATE_DIR.expanduser() / STATUS_NAME


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_status(path: Path, payload: dict[str, Any]) -> None:
    """Write the status file atomically, stamped with the UTC time.

    Args:
        path: Status-file path.
        payload: What happened (``stage``, ``exit``, ``problems``...).

    Raises:
        OSError: When the file cannot be written.
    """
    _atomic_write_json(path, {**payload, "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def marker_path(state_dir: Path, stage: str, day: date) -> Path:
    """Return the marker path for ``stage`` on ``day``.

    Args:
        state_dir: State directory.
        stage: Stage name (lower-case letters, digits and hyphens).
        day: The local day the stage ran.

    Returns:
        ``<state_dir>/markers/<YYYY-MM-DD>/<stage>.json``.

    Raises:
        ValueError: For a stage name that could escape the marker directory.
    """
    if not _STAGE_RE.match(stage):
        raise ValueError(f"bad stage name {stage!r}")
    return state_dir / "markers" / day.isoformat() / f"{stage}.json"


def read_marker(state_dir: Path, stage: str, day: date) -> dict[str, Any] | None:
    """Return today's marker for ``stage``, or None when it has not run (an unreadable marker counts as run).

    Args:
        state_dir: State directory.
        stage: Stage name.
        day: The day.

    Returns:
        The marker record, or None.
    """
    path = marker_path(state_dir, stage, day)
    if not path.exists():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"exit": 2, "detail": "unreadable marker; not re-run"}
    return rec if isinstance(rec, dict) else {"exit": 2, "detail": "malformed marker; not re-run"}


def record_stage(state_dir: Path, stage: str, day: date, exit_code: int, detail: str = "") -> None:
    """Write the day marker for ``stage``.

    Args:
        state_dir: State directory.
        stage: Stage name.
        day: The day.
        exit_code: The stage's exit code (0, 1, 2...).
        detail: A short note.

    Raises:
        OSError: When the marker cannot be written.
    """
    _atomic_write_json(marker_path(state_dir, stage, day), {"stage": stage, "exit": exit_code, "detail": detail,
                                                            "day": day.isoformat()})


def run_stage_once(state_dir: Path, stage: str, run: Callable[[], int], *, day: date | None = None,
                   status_path: Path | None = None) -> int:
    """Run ``stage`` at most once per day.

    If today's marker exists, ``run`` is not called and the recorded exit code is returned. Otherwise ``run`` is
    called and its exit code is recorded whatever it is; an exception is recorded as exit 2 and re-raised. An exit 2
    is also written to the status file.

    Args:
        state_dir: State directory.
        stage: Stage name.
        run: The stage; returns its exit code.
        day: The day (default: today, local time).
        status_path: Status file for exit 2 (default: ``<state_dir>/status.json``).

    Returns:
        The stage's exit code (fresh or recorded).
    """
    day = day or date.today()
    prior = read_marker(state_dir, stage, day)
    if prior is not None:
        return int(prior.get("exit", 2))
    try:
        code = int(run())
    except BaseException as exc:
        record_stage(state_dir, stage, day, 2, f"raised {type(exc).__name__}")
        write_status(status_path or state_dir / STATUS_NAME, {"stage": stage, "exit": 2,
                                                              "problems": [f"raised {type(exc).__name__}"]})
        raise
    record_stage(state_dir, stage, day, code)
    if code == 2:
        write_status(status_path or state_dir / STATUS_NAME, {"stage": stage, "exit": 2, "problems": []})
    return code


__all__ = [
    "DEFAULT_STATE_DIR",
    "STATUS_NAME",
    "default_status_path",
    "marker_path",
    "read_marker",
    "record_stage",
    "run_stage_once",
    "write_status",
]
