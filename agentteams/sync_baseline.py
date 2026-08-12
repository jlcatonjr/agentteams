"""sync_baseline.py — real-content baseline snapshot writer for native↔canonical sync.

Follows the ``front_matter_baseline`` precedent from ``build-log.json``:
store **actual content** (not a hash) at the moment canonical is materialized
into or absorbed from a given framework, so a later run can classify *why*
something differs rather than just detect *that* it does.

**Storage location** (per DEC.1): ``.agentteams/canonical/sync-baselines/<framework>.json``

Each baseline file is a JSON document containing:

- ``schema_version``: ``"1.0"``
- ``framework``: the native framework name
- ``created_at``: ISO-8601 timestamp
- ``source_dir``: the native source directory path at capture time
- ``agents``: list of agent dicts (same shape as CAI ``agents[]`` entries,
  frozen at the moment of sync — this is the "real content" the classifier
  compares against)

**No-baseline rule**: a canonical directory with no recorded baseline for a
framework is treated identically to ``front_matter_merge.py``'s unknown
baseline: apply nothing automatically, report only.  The classifier module
enforces this; this module just provides the storage and retrieval.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentteams.atomicio import _atomic_write_text

__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "BASELINES_SUBDIR",
    "baseline_path",
    "write_baseline",
    "load_baseline",
    "delete_baseline",
    "has_baseline",
]

#: The baseline format version.  Bumped only if the on-disk shape changes.
BASELINE_SCHEMA_VERSION = "1.0"

#: Subdirectory inside ``.agentteams/canonical/`` where baselines live.
BASELINES_SUBDIR = "sync-baselines"


def baseline_path(canonical_dir: Path, framework: str) -> Path:
    """Return the path to a framework's baseline file.

    Args:
        canonical_dir: The ``.agentteams/canonical/`` directory path.
        framework: The native framework name (e.g. ``"goose"``).

    Returns:
        ``canonical_dir/sync-baselines/<framework>.json``
    """
    return Path(canonical_dir) / BASELINES_SUBDIR / f"{framework}.json"


def write_baseline(
    canonical_dir: Path,
    framework: str,
    native_cai: dict[str, Any],
    *,
    native_source_dir: str = "",
) -> Path:
    """Write a real-content baseline snapshot for a framework.

    Captures the agent-level content from the native deployment's CAI dict
    at the moment of sync.  This is what the classifier later compares
    against to determine *who moved* — the same role ``build-log.json``'s
    ``front_matter_baseline`` plays for template renders.

    Args:
        canonical_dir: The ``.agentteams/canonical/`` directory path.
        framework: The native framework name.
        native_cai: The CAI dict from ``export_to_cai`` of the native
            deployment, captured at the sync moment.
        native_source_dir: The native source directory path (for
            traceability; stored but not used by the classifier).

    Returns:
        The path to the written baseline file.
    """
    bpath = baseline_path(canonical_dir, framework)
    # Extract only the agent content — the rest of the CAI dict (schema_version,
    # created_at, instructions_binding, etc.) is not part of the per-agent
    # sync baseline.  The classifier compares agent fields, not top-level team
    # metadata.
    agents = native_cai.get("agents") or []
    baseline_doc = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "framework": framework,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": native_source_dir,
        "agents": agents,
    }
    bpath.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(bpath, json.dumps(baseline_doc, indent=2) + "\n")
    return bpath


def load_baseline(canonical_dir: Path, framework: str) -> dict[str, Any] | None:
    """Load a framework's baseline, or ``None`` if no baseline exists.

    Args:
        canonical_dir: The ``.agentteams/canonical/`` directory path.
        framework: The native framework name.

    Returns:
        The baseline dict, or ``None`` when no baseline file exists.
        The dict has the same shape as written by ``write_baseline``.
    """
    bpath = baseline_path(canonical_dir, framework)
    if not bpath.is_file():
        return None
    return json.loads(bpath.read_text(encoding="utf-8"))


def delete_baseline(canonical_dir: Path, framework: str) -> bool:
    """Delete a framework's baseline file.

    Args:
        canonical_dir: The ``.agentteams/canonical/`` directory path.
        framework: The native framework name.

    Returns:
        ``True`` if the file was deleted, ``False`` if it didn't exist.
    """
    bpath = baseline_path(canonical_dir, framework)
    if bpath.is_file():
        bpath.unlink()
        return True
    return False


def has_baseline(canonical_dir: Path, framework: str) -> bool:
    """Check whether a baseline exists for a framework.

    Args:
        canonical_dir: The ``.agentteams/canonical/`` directory path.
        framework: The native framework name.

    Returns:
        ``True`` if a baseline file exists.
    """
    return baseline_path(canonical_dir, framework).is_file()
