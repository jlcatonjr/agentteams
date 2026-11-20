"""Which emitted files are unchanged since the last build (carved from ``emit.py`` under CH-07).

``_unmodified_since_build`` reads the build log's recorded hashes and subtracts the files the operator has
customised; it is re-exported from ``agentteams.emit``. ``drift`` is imported lazily, as it was in ``emit``.
"""
from __future__ import annotations

from pathlib import Path


def _unmodified_since_build(output_dir: Path) -> frozenset[str]:
    """Return the rel-paths that still match their recorded build-log hash.

    Args:
        output_dir: The agents directory being written to.

    Returns:
        Rel-paths unmodified since last generation. Empty when there is no build log, or on any
        failure — "assume modified" is the safe default, because reporting template drift on a
        file the operator actually edited would misattribute their own work.
    """
    try:
        from agentteams import drift as _drift

        build_log = _drift.load_build_log(output_dir)
        if _drift.is_interop_marker(build_log):
            return frozenset()  # a projection marker authorises no overwrite (cond 13)
        recorded = set(build_log.get("file_hashes", {}))
        modified = {c["rel_path"] for c in _drift.detect_user_customizations(
            output_dir, build_log=build_log
        )}
    except (FileNotFoundError, ValueError, OSError, KeyError):
        return frozenset()
    return frozenset(recorded - modified)
