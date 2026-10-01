"""projection_marker.py — the honest team marker an interop / multi_sync projection writes.

Native generation records ``<agents dir>/references/build-log.json`` (``build_team._write_run_log``),
and that file is the agentteams **team marker**: fleet discovery, the framework-freshness scan,
``--check``/drift, ``--verify-integrity``, the Claude block's present-sibling denies and the
``confine-run.sh`` launcher's required-entry check all key on it. Interop projections
(``--interop-from … --framework codex``, ``multi_sync`` projection) never wrote one, so a projected
``.codex/agents`` team (researchteam, mathAgents, baseAgent) was invisible to all of them.

:func:`write_projection_marker` writes a minimal marker with ``origin: "interop"`` and EMPTY
``template_hashes``. Rules (plan ``tmp/by-week/2026-W40/self-updating-agents-and-codex-marker.plan.md``,
section B and its binding "Codex marker" conditions):

* **Never clobbers a native build-log** (one without ``origin: "interop"``, or one that does not
  parse). An existing interop marker is refreshed.
* **Write order (security cond 12).** The launcher requires a team's switch, verify-key store and
  rosters once the marker exists, so they are written FIRST, each write-if-absent
  (``O_CREAT|O_EXCL|O_NOFOLLOW``): the switch (mirroring the source team's
  ``enforce_decision_signing``; absent/unreadable source = ``false``, which reads exactly like an
  absent switch), the store sentinel, then the roster stubs (``control_plane_io``). Every required
  entry is then re-checked to be a real file / directory, never a symlink. Any failure: NO marker.
* **Real directories only.** The agents dir, its parent, ``references/`` and the store must not be
  symlinks; the marker is written by temp file + ``os.replace`` inside a checked ``references/``.
* **origin "interop" authorises nothing (cond 13).** ``drift.is_interop_marker`` lets every reader
  report "unverifiable (interop projection: no template hashes)" instead of current/clean, and keeps
  the marker's ``file_hashes`` out of the merge's "unmodified since build" set.
* **Claude targets are excluded.** A claude marker makes the launcher require the gate hook
  (``.claude/hooks/constitutional-gate.py``), which only native generation emits.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Same schema version as the native build-log (``build_team._write_run_log``).
MARKER_SCHEMA_VERSION = "1.5"

#: The ``origin`` value that marks a projection marker (``drift.INTEROP_ORIGIN``).
INTEROP_ORIGIN = "interop"

#: Registry frameworks whose projection gets a marker. agents-md and canonical have no team-marker
#: convention; claude is excluded (see the module docstring).
MARKER_FRAMEWORKS: frozenset[str] = frozenset({"codex", "copilot-vscode", "copilot-cli", "goose"})

_SWITCH_REL = "references/agent-privilege.json"
_MARKER_REL = "references/build-log.json"


@dataclass
class ProjectionMarkerResult:
    """Outcome of :func:`write_projection_marker`.

    Attributes:
        marker: The marker path written, or ``None``.
        control_plane: Control-plane files created before the marker (switch, sentinel, stubs).
        skipped: Why nothing was written (a native build-log, an unsupported framework), or ``""``.
        error: Why the marker was refused after a failed or unsafe write, or ``""``.
        created: True when no marker existed before this call.
    """

    marker: Path | None = None
    control_plane: list[Path] = field(default_factory=list)
    skipped: str = ""
    error: str = ""
    created: bool = False


def _sha16(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _rel_or_abs(path: Path, root: Path) -> str:
    """``path`` relative to ``root`` when inside it (no home-dir leak into a committed file)."""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return str(path)


def existing_marker_origin(agents_dir: Path) -> str | None:
    """Classify the build-log already in ``agents_dir``.

    Args:
        agents_dir: A team's agents dir.

    Returns:
        ``None`` when there is none, ``"interop"`` for a projection marker, ``"native"`` for
        anything else (including an unparseable file or a symlink: never overwritten).
    """
    path = agents_dir / _MARKER_REL
    if not os.path.lexists(path):
        return None
    if path.is_symlink():
        return "native"
    data = _read_json(path)
    if isinstance(data, dict) and data.get("origin") == INTEROP_ORIGIN:
        return INTEROP_ORIGIN
    return "native"


def _unsafe_dir(path: Path) -> str:
    """Return why ``path`` is not a usable real directory (``""`` when it is)."""
    if path.is_symlink():
        return f"{path} is a symlink"
    if path.exists() and not path.is_dir():
        return f"{path} is not a directory"
    return ""


def _source_switch_value(source_dir: Path) -> bool:
    data = _read_json(source_dir / _SWITCH_REL)
    return bool(data.get("enforce_decision_signing")) if isinstance(data, dict) else False


def _write_control_plane(agents_dir: Path, framework: str, enforce: bool) -> list[Path]:
    """Create the switch, store sentinel and roster stubs (each write-if-absent).

    Raises:
        OSError: A create failed, or a path is (or passes through) a symlink.
    """
    from agentteams import control_plane_io
    from agentteams.frameworks._sandbox_emit import (
        VERIFY_KEY_STORE_SENTINEL_REL,
        VERIFY_KEY_STORE_SENTINEL_TEXT,
    )

    refs = agents_dir / "references"
    store = (agents_dir / VERIFY_KEY_STORE_SENTINEL_REL).parent
    created: list[Path] = []
    for directory in (refs, store):
        reason = _unsafe_dir(directory)
        if reason:
            raise OSError(reason)
        directory.mkdir(parents=True, exist_ok=True)
        reason = _unsafe_dir(directory)  # raced into a link between the check and the mkdir
        if reason:
            raise OSError(reason)
    switch = agents_dir / _SWITCH_REL
    payload = json.dumps({
        "enforce_decision_signing": enforce,
        "note": "Projected by agentteams interop from the source team's switch (absent there = "
                "false, the same as no switch). Read by the security gate.",
    }, indent=2) + "\n"
    if not os.path.lexists(switch) and control_plane_io._create_exclusive(switch, payload):
        created.append(switch)
    sentinel = agents_dir / VERIFY_KEY_STORE_SENTINEL_REL
    if not os.path.lexists(sentinel) and control_plane_io._create_exclusive(
            sentinel, VERIFY_KEY_STORE_SENTINEL_TEXT):
        created.append(sentinel)
    created += control_plane_io.write_control_plane_stubs(
        agents_dir, framework, {"enforce_decision_signing": enforce})
    return created


def _control_plane_hole(agents_dir: Path) -> str:
    """Return the first launcher-required entry that is missing or a symlink (``""`` when none)."""
    from agentteams.frameworks._sandbox_emit import (
        CONTROL_PLANE_STUB_TEXT,
        VERIFY_KEY_STORE_SENTINEL_REL,
    )

    store = (agents_dir / VERIFY_KEY_STORE_SENTINEL_REL).parent
    if store.is_symlink() or not store.is_dir():
        return f"{store} is missing or a symlink"
    for rel in (_SWITCH_REL, *(f"references/{n}" for n in CONTROL_PLANE_STUB_TEXT)):
        path = agents_dir / rel
        if path.is_symlink() or not path.is_file():
            return f"{path} is missing or a symlink"
    return ""


def _write_marker_file(refs: Path, text: str) -> Path:
    """Atomically write ``refs/build-log.json`` (temp file + ``os.replace``, never via a link)."""
    target = refs / "build-log.json"
    if target.is_symlink():
        raise OSError(f"{target} is a symlink")
    fd, tmp = tempfile.mkstemp(prefix=".build-log.", suffix=".tmp", dir=refs)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, target)
    except OSError:
        os.unlink(tmp)
        raise
    return target


def _marker_payload(
    agents_dir: Path, framework: str, source_dir: Path, source_framework: str,
    files_written: list[str], agent_slugs: list[str] | None,
) -> dict[str, Any]:
    from agentteams import __version__

    project_root = agents_dir.parent.parent
    source_log_path = source_dir / _MARKER_REL
    source_log = _read_json(source_log_path)
    source_log = source_log if isinstance(source_log, dict) else {}
    written: list[str] = []
    hashes: dict[str, str] = {}
    for raw in files_written:
        path = Path(raw)
        if not path.is_file():
            continue
        written.append(_rel_or_abs(path, project_root))
        hashes[os.path.relpath(path.resolve(), agents_dir.resolve())] = _sha16(path)
    slugs = source_log.get("agent_slug_list") or list(agent_slugs or [])
    return {
        "schema_version": MARKER_SCHEMA_VERSION,
        "agentteams_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_name": source_log.get("project_name"),
        "agent_slug_list": slugs,
        "framework": framework,
        "files_written": written,
        "file_hashes": hashes,
        "template_hashes": {},
        "origin": INTEROP_ORIGIN,
        "source_dir": _rel_or_abs(source_dir, project_root),
        "source_framework": source_framework,
        "source_build_log_sha256": (
            hashlib.sha256(source_log_path.read_bytes()).hexdigest()
            if source_log_path.is_file() and not source_log_path.is_symlink() else None),
    }


def write_projection_marker(
    agents_dir: Path,
    framework: str,
    *,
    source_dir: Path,
    source_framework: str,
    files_written: list[str],
    agent_slugs: list[str] | None = None,
) -> ProjectionMarkerResult:
    """Write (or refresh) the ``origin: "interop"`` team marker of a projected team.

    Args:
        agents_dir: The projected team's agents dir (e.g. ``<root>/.codex/agents``).
        framework: The target registry framework id.
        source_dir: The source team's agents dir (or the canonical dir).
        source_framework: The source framework id.
        files_written: Absolute paths this projection wrote (hashed into ``file_hashes``). When
            empty and an interop marker already exists, that marker is left as it is.
        agent_slugs: Fallback ``agent_slug_list`` when the source has no build-log.

    Returns:
        A :class:`ProjectionMarkerResult`. ``error`` is set (and no marker written) when the
        control plane could not be written safely; ``skipped`` when a native build-log exists or
        the framework has no marker convention here.
    """
    result = ProjectionMarkerResult()
    if framework not in MARKER_FRAMEWORKS:
        result.skipped = f"{framework}: no projection-marker convention"
        return result
    origin = existing_marker_origin(agents_dir)
    if origin == "native":
        result.skipped = f"{agents_dir / _MARKER_REL} is a native build-log; left untouched"
        return result
    if origin == INTEROP_ORIGIN and not files_written:
        result.skipped = "nothing projected; the existing interop marker is kept"
        return result
    for directory in (agents_dir.parent, agents_dir):
        reason = _unsafe_dir(directory)
        if reason or not directory.is_dir():
            result.error = f"refusing the projection marker: {reason or f'{directory} does not exist'}"
            return result
    try:
        result.control_plane = _write_control_plane(
            agents_dir, framework, _source_switch_value(source_dir))
    except OSError as exc:
        result.error = f"refusing the projection marker: control-plane write failed: {exc}"
        return result
    hole = _control_plane_hole(agents_dir)
    if hole:
        result.error = f"refusing the projection marker: {hole} (the launcher would refuse it)"
        return result
    payload = _marker_payload(agents_dir, framework, source_dir, source_framework,
                              files_written, agent_slugs)
    try:
        result.marker = _write_marker_file(agents_dir / "references",
                                           json.dumps(payload, indent=2) + "\n")
    except OSError as exc:
        result.error = f"refusing the projection marker: {exc}"
        return result
    result.created = origin is None
    return result


def mark_interop_projection(
    result: Any, cai: dict[str, Any], source_dir: Path, target_framework: str, target_dir: Path,
) -> None:
    """Write an interop run's team marker and record the outcome on its result.

    Called by ``interop.run_interop`` after a real (not dry-run, not skills-only), error-free run.

    Args:
        result: The run's ``interop.InteropResult`` (mutated: ``errors``, ``marker_files``,
            ``notices``).
        cai: The CAI document the run imported (source framework and agent slugs).
        source_dir: The source team's agents dir.
        target_framework: The target registry framework id.
        target_dir: The target agents dir.
    """
    marker = write_projection_marker(
        target_dir, target_framework, source_dir=source_dir,
        source_framework=str(cai.get("source_framework", "unknown")),
        files_written=list(result.converted),
        agent_slugs=[str(a.get("slug")) for a in cai.get("agents", []) if a.get("slug")],
    )
    if marker.error:
        result.errors.append(f"{marker.error}. NO team marker was written.")
        return
    result.marker_files += [str(p) for p in marker.control_plane]
    if marker.marker is not None:
        result.marker_files.append(str(marker.marker))
        if marker.created and target_framework == "codex":
            result.notices.append(codex_session_advisory(target_dir.parent.parent))


def codex_session_advisory(project_root: Path) -> str:
    """The cond-14 advisory text: once ``.codex`` is a team, project into it outside Claude.

    Args:
        project_root: The project root holding ``.codex``.

    Returns:
        The advisory line.
    """
    return (
        f"{project_root / '.codex'} is now a recognised agentteams team: a Claude sandbox block "
        "generated from here on write-denies .codex whole, so an in-session (Claude Bash) interop "
        "into .codex/agents will fail. Run the projection outside any Claude session, e.g. "
        "`agentteams --interop-from <source> --framework codex --output . --overwrite`."
    )


def print_marker_outcome(result: ProjectionMarkerResult, *, label: str = "") -> None:
    """Print one line describing ``result`` (the error, if any, to stderr).

    Args:
        result: From :func:`write_projection_marker`.
        label: A prefix such as ``"[codex] "``.
    """
    if result.error:
        print(f"  ✗  {label}{result.error}. NO team marker was written.", file=sys.stderr)
    elif result.marker is not None:
        print(f"  ✓  {label}projection marker (origin: interop) written: {result.marker}")


__all__ = [
    "INTEROP_ORIGIN",
    "MARKER_FRAMEWORKS",
    "MARKER_SCHEMA_VERSION",
    "ProjectionMarkerResult",
    "codex_session_advisory",
    "existing_marker_origin",
    "mark_interop_projection",
    "print_marker_outcome",
    "write_projection_marker",
]
