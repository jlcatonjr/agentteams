"""sync_pin.py — the pinned-source contract for multi-framework sync.

The multi-framework sync feature designates one framework as the **bootstrap
pin**: the framework whose state seeds the canonical hub at init, and — per the
operator's locked decision — the framework that **always wins** a genuine
conflict thereafter (``both-moved-conflict``), even against itself.

This module owns the on-disk contract only (read/write/locate). The
reconciliation policy that *uses* the pin lives in :mod:`agentteams.multi_sync`.

Storage: ``<root>/.agentteams/pin.json`` — a small JSON document:

- ``schema_version``: ``"1.0"``
- ``pinned_framework``: the bootstrap pin (authoritative on conflict)
- ``frameworks``: every framework kept in sync (the sync set)
- ``canonical_dir``: canonical hub path, relative to ``root``
- ``last_synced_commit``: git SHA of the last successful sync (change detection anchor)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentteams.atomicio import _atomic_write_text

__all__ = [
    "PIN_SCHEMA_VERSION",
    "PIN_SUBPATH",
    "DEFAULT_CANONICAL_REL",
    "pin_path",
    "read_pin",
    "save_pin",
    "update_last_synced_commit",
]

#: On-disk pin document version.
PIN_SCHEMA_VERSION = "1.0"

#: Location of the pin document relative to the project root.
PIN_SUBPATH = ".agentteams/pin.json"

#: Default canonical hub location relative to the project root.
DEFAULT_CANONICAL_REL = ".agentteams/canonical"


def pin_path(root: Path) -> Path:
    """Return the pin document path for a project root.

    Args:
        root: The project root directory.

    Returns:
        ``root/.agentteams/pin.json``.
    """
    return Path(root) / PIN_SUBPATH


def read_pin(root: Path) -> dict[str, Any] | None:
    """Load the pin document, or ``None`` if the project is not pinned.

    Args:
        root: The project root directory.

    Returns:
        The pin dict, or ``None`` when no pin document exists.

    Raises:
        ValueError: If the pin document exists but is malformed JSON or is
            missing required keys.
    """
    path = pin_path(root)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"pin document is unreadable: {path} ({exc})") from exc
    if not isinstance(doc, dict) or not doc.get("pinned_framework"):
        raise ValueError(f"pin document is malformed (no pinned_framework): {path}")
    return doc


def save_pin(
    root: Path,
    *,
    pinned_framework: str,
    frameworks: list[str],
    canonical_dir: str = DEFAULT_CANONICAL_REL,
    last_synced_commit: str = "",
) -> Path:
    """Write (or overwrite) the pin document atomically.

    Args:
        root: The project root directory.
        pinned_framework: The bootstrap pin (must appear in ``frameworks``).
        frameworks: The full sync set.
        canonical_dir: Canonical hub path relative to ``root``.
        last_synced_commit: Git SHA anchoring change detection (may be empty
            at init before the first sync).

    Returns:
        The path to the written pin document.

    Raises:
        ValueError: If ``pinned_framework`` is not in ``frameworks``.
    """
    if pinned_framework not in frameworks:
        raise ValueError(
            f"pinned_framework {pinned_framework!r} must be in the sync set "
            f"{frameworks!r}"
        )
    doc = {
        "schema_version": PIN_SCHEMA_VERSION,
        "pinned_framework": pinned_framework,
        "frameworks": list(frameworks),
        "canonical_dir": canonical_dir,
        "last_synced_commit": last_synced_commit,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = pin_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(doc, indent=2) + "\n")
    return path


def update_last_synced_commit(root: Path, commit: str) -> Path:
    """Advance the change-detection anchor after a successful sync.

    Args:
        root: The project root directory.
        commit: The git SHA to record as the last synced commit.

    Returns:
        The path to the updated pin document.

    Raises:
        ValueError: If the project is not pinned.
    """
    doc = read_pin(root)
    if doc is None:
        raise ValueError(f"cannot update: no pin document at {pin_path(root)}")
    return save_pin(
        root,
        pinned_framework=doc["pinned_framework"],
        frameworks=doc["frameworks"],
        canonical_dir=doc.get("canonical_dir", DEFAULT_CANONICAL_REL),
        last_synced_commit=commit,
    )
