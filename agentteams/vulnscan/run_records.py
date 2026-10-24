"""Per-run agent records: a later agent run on the same day never overwrites an earlier run's record.

Several agent runs can share one run directory (``daily`` with ``agent.enabled``, then a hand-run
``agent-run``). The latest run's record stays where readers look for it: ``<run>/agent/status.json``,
``<run>/agent/transcript.log``, ``<run>/gateway/spend.json`` and ``<run>/agent-report/report.md``.
Before a run writes anything, :func:`archive_previous` moves the previous run's files to
``<run>/agent/runs/<run id>/`` (the report to ``<run>/agent-report/<run id>.report.md``, so it keeps
the report store's protections). Called under the run lock, so a report found at the end of a run
was submitted by that run.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from agentteams.vulnscan import report_store

RUNS_DIR = "runs"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _previous_id(status: Path) -> str:
    """Return the run id recorded in ``status`` when it is well-formed, else ``earlier-<UTC stamp>``."""
    try:
        rid = json.loads(status.read_text(encoding="utf-8")).get("run_id")
    except (OSError, ValueError, AttributeError):
        rid = None
    if isinstance(rid, str) and _ID_RE.match(rid):
        return rid
    return "earlier-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def archive_previous(run_dir: Path) -> str | None:
    """Move the previous agent run's status, transcript, spend and report aside (call under the run lock).

    Renames only (a planted symlink is moved, never followed). A name already used gets a ``-<n>`` suffix.

    Args:
        run_dir: The run directory.

    Returns:
        The archive name (``<run>/agent/runs/<name>``), or None when there was nothing to archive.

    Raises:
        OSError: A rename failed.
        ValueError: The stored report's directory is unsafe (:mod:`.report_store`).
    """
    out = run_dir / "agent"
    files = [p for p in (out / "status.json", out / "transcript.log", run_dir / "gateway" / "spend.json")
             if p.is_file() or p.is_symlink()]
    has_report = (run_dir / report_store.REPORT_DIR / report_store.REPORT_FILE).exists()
    if not files and not has_report:
        return None
    base = _previous_id(out / "status.json")
    name, n = base, 1
    while (out / RUNS_DIR / name).exists() or (run_dir / report_store.REPORT_DIR / f"{name}.report.md").exists():
        n += 1
        name = f"{base}-{n}"
    dest = out / RUNS_DIR / name
    dest.mkdir(parents=True)
    for p in files:
        p.rename(dest / p.name)
    if has_report:
        report_store.archive(run_dir, name)
    return name


def run_dirs_of(run_dir: Path) -> list[Path]:
    """Return the archived agent-run record directories of a run directory, oldest first.

    Args:
        run_dir: The run directory.

    Returns:
        ``<run>/agent/runs/*`` directories ordered by modification time.
    """
    base = run_dir / "agent" / RUNS_DIR
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir() and not p.is_symlink()), key=lambda p: p.stat().st_mtime)
