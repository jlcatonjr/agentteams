"""Scan mirrors: read a repository's default branch from a detached worktree, and report it under the checkout.

The daily scan reads each root's working tree and never fetches, so a checkout kept on an old feature branch is
scanned as it was weeks ago: its findings are stale, and the fix plans built from it (which ``retry_guard`` keys holds
on) do not change until someone pulls. With the config key ``scan_mirror`` set to a worktree name (for example
``"vulnscan-mirror"``), the scan reads ``<repo>.worktrees/<name>`` instead, a detached worktree that an operator job
moves to ``origin/HEAD`` before the scan, and every result (findings, fix plans, the freshness row) stays named
after the checkout, so handoffs, holds and the executor see the repository they always saw.

A mirror is used only when the refresh job recorded it fresh **today**: ``<state>/scan-mirrors/<YYYY-MM-DD>.json``
maps a checkout path to ``"ok <sha>"`` (or ``"failed <reason>"``). Anything else (no key, no file, a failed fetch, a
missing or symlinked directory) scans the checkout as before, and the freshness row says which was read.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agentteams.vulnscan import paths

STAMP_DIR = "scan-mirrors"


def stamp_path(day: str) -> Path:
    """Return the refresh job's record for ``day``.

    Args:
        day: ``YYYY-MM-DD`` (the run's local date).

    Returns:
        ``<state>/scan-mirrors/<day>.json``.
    """
    return paths.state_dir() / STAMP_DIR / f"{day}.json"


def mirror_for(cfg: dict[str, Any], root: Path, day: str) -> Path | None:
    """Return the mirror to scan for ``root`` today, or None to scan the checkout itself.

    Args:
        cfg: Daily config (``scan_mirror``: a worktree name, "" = off).
        root: The checkout (a ``repo_roots`` entry or a registry ``fix_checkout``).
        day: The run's date, ``YYYY-MM-DD``.

    Returns:
        ``<root>.worktrees/<scan_mirror>`` when the key is set, the directory is real, and today's record says
        ``ok`` for this checkout; else None.
    """
    name = str(cfg.get("scan_mirror") or "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None
    mirror = root.parent / f"{root.name}.worktrees" / name
    if mirror.is_symlink() or not mirror.is_dir():
        return None
    try:
        record = json.loads(stamp_path(day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    state = record.get(str(root), record.get(str(root.resolve())))
    return mirror if isinstance(state, str) and state.startswith("ok ") else None


def also_under(skips: Iterable[Path], root: Path, mirror: Path) -> list[Path]:
    """Return ``skips`` plus, for each one inside ``root``, the same path inside ``mirror``.

    An exclusion or an opted-out checkout must hold in the mirror exactly as in the checkout.

    Args:
        skips: The operator's resolved exclude paths (or opted-out checkouts).
        root: The checkout.
        mirror: Its mirror.

    Returns:
        The paths, each one inside ``root`` also named under ``mirror``.
    """
    out: list[Path] = []
    base = root.resolve()
    for p in skips:
        out.append(p)
        if p.is_relative_to(base):
            out.append(mirror.resolve() / p.relative_to(base))
    return out
