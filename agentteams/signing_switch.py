"""signing_switch.py — keep every rendered ``agent-privilege.json`` switch tracking the description.

The strict agent-privilege switch (``<agents dir>/references/agent-privilege.json``, key
``enforce_decision_signing``) is a governance control (Rule 15 / C-3 adjacent): the security gate
reads it to decide whether an unsigned authorizing decision row is refused. Two writers render it:

* native generation (``cli/artifacts._write_agent_privilege_config``), from the manifest value
  (``analyze.build_manifest``: an absent description key means ``true``); and
* interop / multi-sync projection (``projection_marker``), mirroring the source team's switch.

Until 2026-10-11 the projection wrote the switch only when it was absent, so an operator's
``"enforce_decision_signing": false`` reached ``.github/agents`` but never the projected
``.codex/agents`` (fleet-sync round-3 security review). This module is the one place both writers
now go through:

* :func:`apply_switch` rewrites a switch the pipeline OWNS (the team carries a build-log / interop
  marker AND the existing switch carries one of the pipeline's own notes) so it tracks the
  rendered value. A switch whose ownership is unclear (no team marker, a foreign note, an
  unparseable or non-regular file) is KEPT, and a notice says so whenever the values differ.
* A ``true -> false`` change is never silent: it prints a :data:`RELAXED_PREFIX` line, which the
  fleet classifies as a REVIEW reason (:func:`review_lines`). A kept, divergent switch prints a
  :data:`KEPT_PREFIX` line, also a REVIEW reason.
* :func:`sync_projected_switches` is called after a native render: every sibling team in the same
  project that is an interop projection OF this team gets its switch brought into line with the
  value now ON DISK in this team (never the bare description value: a kept, hand-pinned source
  switch is what a re-projection would mirror too), so the next fleet update carries it to the
  projected surfaces.
* A signing-governed team (``signing-governed.marker``) never gets ``false``: the switch stays
  (or is created) ``true`` and a :data:`KEPT_PREFIX` notice reports the conflict.

Writes never follow a symlink: the ``references/`` dir is opened ``O_DIRECTORY|O_NOFOLLOW`` and
the switch is replaced by a temp file + ``os.replace`` relative to that descriptor.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.drift import is_interop_marker
from agentteams.frameworks._sandbox_emit import TEAM_MARKER_REL

#: The switch, relative to a team's agents dir.
SWITCH_REL = "references/agent-privilege.json"

#: A signing-governed team's assertion marker (``cli/decision_log``): its switch is always ``true``.
GOVERNED_MARKER = "signing-governed.marker"

#: Notice prefix for a ``true -> false`` change; a fleet REVIEW reason.
RELAXED_PREFIX = "SIGNING SWITCH RELAXED"

#: Notice prefix for a switch left as it is although it differs from the rendered value (ownership
#: unclear, unusable file, governed team, failed write); a fleet REVIEW reason.
KEPT_PREFIX = "SIGNING SWITCH KEPT"

#: The note native generation writes (``cli/artifacts._write_agent_privilege_config``).
NATIVE_NOTE = (
    "Strict agent-privilege switch. When true, an unsigned AUTHORIZING "
    "security-decision row is refused (fail-closed) rather than trusted on its "
    "author name. Read by the security gate. Turn off by setting "
    "enforce_decision_signing:false in the project brief and re-running --update."
)

#: The note an interop / multi-sync projection writes (``projection_marker``).
PROJECTION_NOTE = (
    "Projected by agentteams interop from the source team's switch (absent there = "
    "false, the same as no switch; a signing-governed team is always true). Read by "
    "the security gate."
)

#: Notes that identify a switch the pipeline wrote (any other note is hand-authored).
PIPELINE_NOTES: frozenset[str] = frozenset({NATIVE_NOTE, PROJECTION_NOTE})

#: Project-relative team dirs a native render may have projected siblings in.
TEAM_DIRS: tuple[str, ...] = (".github/agents", ".claude/agents", ".goose/recipes", ".codex/agents")

#: Unanchored: a caller may prefix the line (``[codex] ``, ``Notice:``); a false match only adds a
#: REVIEW, a missed one would hide a relax.
_REVIEW_RE = re.compile(rf"({RELAXED_PREFIX}|{KEPT_PREFIX})\b")


@dataclass
class SwitchOutcome:
    """What :func:`apply_switch` did.

    Attributes:
        path: The switch path.
        written: True when the file was created or rewritten.
        kept: True when a divergent switch was left in place.
        relaxed: True when an owned switch went ``true -> false``.
        notices: Lines to print (already prefixed; empty when nothing noteworthy happened).
    """

    path: Path
    written: bool = False
    kept: bool = False
    relaxed: bool = False
    notices: list[str] = field(default_factory=list)


def _read_regular_text(path: Path) -> str:
    """Read ``path`` opened ``O_NOFOLLOW`` after an lstat regular-file check.

    Raises:
        ValueError: A symlink, a non-regular file, or unreadable.
    """
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise ValueError(f"{path} is a symlink or not a regular file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise ValueError(f"{path} is unreadable: {exc}") from exc


def _parse_switch(text: str, path: Path) -> dict:
    """Parse switch text; raise ValueError unless it is an object with a boolean value."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"switch {path} is malformed: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("enforce_decision_signing"), bool):
        raise ValueError(f"switch {path} has no boolean enforce_decision_signing")
    return data


def read_switch(path: Path) -> bool | None:
    """Return a switch's ``enforce_decision_signing``; ``None`` when absent.

    Args:
        path: The switch file.

    Returns:
        The boolean value, or ``None`` when nothing (not even a dangling link) is there.

    Raises:
        ValueError: The switch is present but a symlink, not a regular file, unreadable,
            malformed, or its value is not a JSON boolean (never defaulted to false).
    """
    if not os.path.lexists(path):
        return None
    return _parse_switch(_read_regular_text(path), path)["enforce_decision_signing"]


def _switch_state(agents_dir: Path) -> tuple[bool | None, str]:
    """Read ``agents_dir``'s switch for :func:`self_governance_refusal`.

    Raises:
        ValueError: The switch is present but unusable (see :func:`read_switch`).
    """
    value = read_switch(agents_dir / SWITCH_REL)
    return value, {True: "ON", False: "OFF", None: "missing"}[value]


def self_governance_refusal(agents_dir: Path, canonical_dir: Path) -> str | None:
    """Return why a ``--self`` run into ``agents_dir`` must refuse, or ``None`` when it may proceed.

    ``--self`` reads its governance (this switch, the decision log, the Rule 15(d) cap) from the
    target surface's ``references/``; before ``--self`` followed ``--framework`` it always read the
    canonical self surface (``.github/agents``). The run refuses only when the target is WEAKER than
    that canonical surface: canonical ON while the target is OFF or missing (a missing switch means
    legacy unsigned). Both missing (a fresh clone, the switch is gitignored) is the pre-existing
    behaviour and proceeds, as does the target being the canonical directory itself. A present but
    unusable switch on either side refuses.

    Args:
        agents_dir: The resolved ``--self`` agents directory.
        canonical_dir: The canonical self surface (``<module root>/.github/agents``).

    Returns:
        The refusal message, or ``None`` when the run may proceed.

    Raises:
        Nothing; an unreadable or malformed switch is reported as a refusal.
    """
    try:
        target, target_state = _switch_state(agents_dir)
        canonical, canonical_state = _switch_state(canonical_dir)
    except ValueError as exc:
        return f"--self cannot read a signing switch: {exc}. Fix or remove it."
    if target is True or canonical is not True:
        return None
    return (
        f"--self targets {agents_dir} (signing switch {target_state}), weaker than the canonical self "
        f"surface {canonical_dir} (signing switch {canonical_state}). --self reads its governance from "
        "the target, so it refuses rather than run with signing relaxed. Render or seed that surface's "
        'governance first (references/agent-privilege.json with "enforce_decision_signing": true), or '
        "use a framework whose surface is governed (e.g. the default copilot-vscode, .github/agents)."
    )


def switch_payload(value: bool, note: str) -> str:
    """Render the switch file text.

    Args:
        value: The ``enforce_decision_signing`` value.
        note: The pipeline note (:data:`NATIVE_NOTE` or :data:`PROJECTION_NOTE`).

    Returns:
        The JSON text, newline-terminated.
    """
    return json.dumps({"enforce_decision_signing": bool(value), "note": note}, indent=2) + "\n"


def _has_team_marker(agents_dir: Path) -> bool:
    marker = agents_dir / TEAM_MARKER_REL
    return not marker.is_symlink() and marker.is_file()


def owns_switch(agents_dir: Path) -> bool:
    """Return True when the pipeline owns ``agents_dir``'s existing switch.

    Owned means BOTH a team marker (a regular ``references/build-log.json``, native or interop)
    AND an existing switch that is a regular JSON file carrying one of :data:`PIPELINE_NOTES`.
    Anything else — no marker, a hand-written note, an unparseable file, a link — is unclear.

    Args:
        agents_dir: A team's agents dir.

    Returns:
        True when the switch may be rewritten to track the rendered value.
    """
    path = agents_dir / SWITCH_REL
    try:
        data = _parse_switch(_read_regular_text(path), path)
    except ValueError:
        return False
    return _has_team_marker(agents_dir) and data.get("note") in PIPELINE_NOTES


def is_governed(*dirs: Path) -> bool:
    """Return True when any of ``dirs`` holds :data:`GOVERNED_MARKER`.

    Args:
        dirs: Team agents dirs.

    Returns:
        True for a signing-governed team.
    """
    return any(os.path.lexists(d / GOVERNED_MARKER) for d in dirs)


def _replace_no_follow(path: Path, text: str) -> None:
    """Atomically write ``path`` (inside a real ``references/`` dir), never through a link.

    Raises:
        OSError: ``references/`` is not a real directory, or the switch is a link / non-regular.
    """
    refs = path.parent
    refs.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(os.lstat(refs).st_mode):
        raise OSError(f"{refs} is not a real directory (symlink?)")
    dir_fd = os.open(refs, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    tmp = f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    try:
        try:
            st = os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            st = None
        if st is not None and not stat.S_ISREG(st.st_mode):
            raise OSError(f"{path} is a symlink or not a regular file; left untouched")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o644, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except OSError:
            os.unlink(tmp, dir_fd=dir_fd)
            raise
    finally:
        os.close(dir_fd)


def _js(value: bool) -> str:
    return "true" if value else "false"


def apply_switch(
    agents_dir: Path, value: bool, note: str, *, label: str = "", create_only: bool = False,
) -> SwitchOutcome:
    """Make ``agents_dir``'s switch track ``value`` when the pipeline owns it.

    An absent switch is created. An owned switch (:func:`owns_switch`) is rewritten when its text
    differs; a ``true -> false`` change adds a :data:`RELAXED_PREFIX` notice. A switch of unclear
    ownership is never touched; when its value differs from ``value`` (or it cannot be read) a
    :data:`KEPT_PREFIX` notice is added. A signing-governed team never gets ``false``: ``true`` is
    written (or kept) instead, with a :data:`KEPT_PREFIX` notice.

    Args:
        agents_dir: The team's agents dir.
        value: The rendered ``enforce_decision_signing`` value.
        note: The note to write (:data:`NATIVE_NOTE` or :data:`PROJECTION_NOTE`).
        label: A prefix for notices (e.g. ``"[codex] "``).
        create_only: Only create an absent switch; never rewrite an existing one (a projection
            whose source has no switch carries no value to track).

    Returns:
        A :class:`SwitchOutcome`.

    Raises:
        OSError: The write failed, or the switch / ``references/`` is a symlink.
    """
    path = agents_dir / SWITCH_REL
    outcome = SwitchOutcome(path=path)
    if not value and is_governed(agents_dir):
        value = True
        outcome.notices.append(
            f"  ⚠  {KEPT_PREFIX}: {label}{path} stays true: the team is signing-governed "
            f"({GOVERNED_MARKER}), so enforce_decision_signing: false is not applied.")
    text = switch_payload(value, note)
    try:
        current_text = _read_regular_text(path) if os.path.lexists(path) else None
        current = (None if current_text is None
                   else _parse_switch(current_text, path)["enforce_decision_signing"])
    except ValueError as exc:
        outcome.kept = True
        outcome.notices.append(
            f"  ⚠  {KEPT_PREFIX}: {label}{path} is unusable ({exc}); left as it is, not set to "
            f"{_js(value)}. Fix or remove it by hand, then re-run --update.")
        return outcome
    if current is None:
        _replace_no_follow(path, text)
        outcome.written = True
        return outcome
    if create_only or current_text == text:
        return outcome
    if not owns_switch(agents_dir):
        if current != value:
            outcome.kept = True
            outcome.notices.append(
                f"  ⚠  {KEPT_PREFIX}: {label}{path} is {_js(current)} but the description "
                f"renders {_js(value)}; ownership is unclear (no agentteams team marker, or a "
                "hand-written switch), so it was NOT changed. Review it by hand.")
        return outcome
    _replace_no_follow(path, text)
    outcome.written = True
    if current and not value:
        outcome.relaxed = True
        outcome.notices.append(
            f"  ⚠  {RELAXED_PREFIX}: {label}{path} true -> false (enforce_decision_signing). "
            "Unsigned authorizing decision rows will be trusted on their author name again. "
            "Confirm this was intended.")
    elif value and not current:
        outcome.notices.append(f"  ℹ  {label}{path}: signing switch tightened false -> true.")
    return outcome


def _team_rel(agents_dir: Path) -> str | None:
    """``agents_dir`` as a known project-relative team dir (``.github/agents``, ...), else None."""
    parts = Path(agents_dir).resolve().parts
    rel = "/".join(parts[-2:]) if len(parts) >= 2 else ""
    return rel if rel in TEAM_DIRS else None


def _projected_from(agents_dir: Path) -> str | None:
    """The ``source_dir`` of ``agents_dir``'s interop marker, or None when it is not one."""
    marker = agents_dir / TEAM_MARKER_REL
    try:
        data = json.loads(_read_regular_text(marker)) if os.path.lexists(marker) else None
    except ValueError:
        return None
    src = data.get("source_dir") if is_interop_marker(data) else None
    return src if isinstance(src, str) else None


def _contained_real_dir(path: Path, root: Path) -> bool:
    """True when ``path`` is a real directory under ``root`` with no symlinked component below it."""
    rel = path.relative_to(root)
    cur = root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink() or not cur.is_dir():
            return False
    return path.resolve().is_relative_to(root.resolve())


def sync_projected_switches(agents_dir: Path) -> list[str]:
    """Bring every sibling interop projection of this team into line with this team's switch.

    Called after a native render wrote (or kept) its own switch. The value propagated is the one
    now ON DISK in ``agents_dir`` — the value a re-projection would mirror — never the bare
    description value, so a kept, hand-pinned source switch is not contradicted. A sibling team
    (another :data:`TEAM_DIRS` entry under the same project, reached through real directories
    only) whose interop marker names THIS team as its ``source_dir`` is re-pointed through
    :func:`apply_switch` (a hand-written switch there is kept and reported, never flipped; a
    governed one stays ``true``). Native siblings and projections of another source are left alone.

    Args:
        agents_dir: The natively rendered team's agents dir.

    Returns:
        Notice lines to print (relaxed / kept / failed / tightened).

    Raises:
        Nothing; a failed sibling write is reported as a :data:`KEPT_PREFIX` notice.
    """
    own = _team_rel(agents_dir)
    if own is None:
        return []
    try:
        value = read_switch(agents_dir / SWITCH_REL)
    except ValueError:
        return []  # this team's own switch is unusable; apply_switch already reported it
    if value is None:
        return []
    root = Path(agents_dir).resolve().parent.parent
    notices: list[str] = []
    for rel in TEAM_DIRS:
        sibling = root / rel
        if rel == own or not sibling.is_dir() or not _contained_real_dir(sibling, root):
            continue
        if _projected_from(sibling) != own or not os.path.lexists(sibling / SWITCH_REL):
            continue
        try:
            outcome = apply_switch(sibling, value or is_governed(agents_dir), PROJECTION_NOTE,
                                   label=f"[{rel}] ")
        except OSError as exc:
            notices.append(f"  ⚠  {KEPT_PREFIX}: [{rel}] projected switch not updated: {exc}")
            continue
        notices += outcome.notices
    return notices


def review_lines(update_output: str) -> list[str]:
    """Return the signing-switch REVIEW lines (relaxed / kept) in a target's update output.

    Args:
        update_output: The captured output of one fleet target's update.

    Returns:
        The matching lines, stripped.
    """
    return [line.strip() for line in update_output.splitlines() if _REVIEW_RE.search(line)]


def review_reason(lines: list[str]) -> str:
    """Summarise :func:`review_lines` output as one fleet REVIEW reason ("" when none).

    Args:
        lines: Lines from :func:`review_lines`.

    Returns:
        ``"signing switch relaxed (...)"`` and/or ``"signing switch kept (...)"``.
    """
    relaxed = [l for l in lines if RELAXED_PREFIX in l]
    kept = [l for l in lines if KEPT_PREFIX in l]
    return "; ".join(filter(None, (
        f"signing switch relaxed ({len(relaxed)}) — confirm true -> false was intended"
        if relaxed else "",
        f"signing switch kept, not tracking the description ({len(kept)}) — review by hand"
        if kept else "")))


__all__ = [
    "GOVERNED_MARKER",
    "KEPT_PREFIX",
    "NATIVE_NOTE",
    "PIPELINE_NOTES",
    "PROJECTION_NOTE",
    "RELAXED_PREFIX",
    "SWITCH_REL",
    "SwitchOutcome",
    "TEAM_DIRS",
    "TEAM_MARKER_REL",
    "apply_switch",
    "is_governed",
    "owns_switch",
    "read_switch",
    "review_lines",
    "review_reason",
    "switch_payload",
    "sync_projected_switches",
]
