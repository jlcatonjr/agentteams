"""sandbox_projection.py — project the model-substrate WASM switch into a consumer project (P20, D1 "both").

A brief can declare, with ``wasm_sandbox.model_substrate: "on"|"off"`` (or ``--wasm-substrate``), that the project's
*model-substrate* code (for example baseAgent's ABM ports compiled to WASM) must run WASM-confined. agentteams never
runs or confines that code: it renders the decision, with the effective OS/WASM state of the render, into one small
machine-readable file, ``<project>/.agentteams/sandbox-state.json`` (schema ``agentteams-sandbox-state/v1``,
``agentteams/schemas/sandbox-state.schema.json``). The consumer reads it as the default and the floor of its own
``--wasm`` switch and enforces it (Layer D emits, Layers A-C enforce). The switch is independent of
``wasm_sandbox.enabled``, which confines *agent* code here.

Contract (every rule fails closed):

* **Opt-in.** The file is written only when the brief or argv sets the switch; every other render is byte-identical.
* **Enumerated values only (C-4).** No brief text is copied in: the reasons are the fixed ``SandboxState`` vocabulary.
  No limits, paths, grants or provider names are projected, so a brief cannot loosen the consumer's certified grant.
* **Ownership.** Only a file whose ``producer`` is ``"agentteams"`` is rewritten; a foreign file is kept with a
  ``SANDBOX STATE KEPT`` notice. A record that differs from the existing file only in ``team`` is not rewritten; one
  that differs in a value *and* comes from another team prints ``SANDBOX STATE CHANGED by <team>``.
* **No silent drop.** When the switch becomes unset, the pipeline-owned file is left in place (a notice says so), and
  its value is carried as the *kept* floor (``wasm_config.WASM_SUBSTRATE_KEPT_KEY``) so the relaxation gate sees no
  change. Retiring the floor (deleting the file) or lowering it to ``off`` is ``sandbox-wasm-off:model-substrate``.
* **No-follow writes.** ``.agentteams`` must be a real directory and the file a regular file (or absent); the write
  is temp-then-``os.replace`` relative to the directory fd (``signing_switch._replace_no_follow``).

Rule-12 residuals (audit targets): the kept floor can only hold a stricter value longer, never a looser one; the
file is session-write-protected only where an OS sandbox is emitted, and with the OS sandbox off its integrity rests
on :func:`verify_projection` (``--verify-integrity``) and on the build-log record the gate ORs with the file.

Integrity-pinned (a silent edit would lower a floor the consumer trusts).
"""

from __future__ import annotations

import json
import os
import stat
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The projected file, relative to the project root.
PROJECTION_REL = ".agentteams/sandbox-state.json"
#: The schema id carried in the file (``schema``).
SCHEMA_ID = "agentteams-sandbox-state/v1"
#: The only producer whose file agentteams rewrites.
PRODUCER = "agentteams"
#: The fields, in file order (the schema's ``required`` list).
FIELDS: tuple[str, ...] = (
    "schema", "producer", "agentteams_version", "team", "os_sandbox", "os_sandbox_reason", "wasm_sandbox",
    "wasm_sandbox_reason", "model_substrate_wasm", "model_substrate_wasm_source", "require_enforced",
)
#: Fields whose difference alone never causes a rewrite (who wrote it, not what it says).
_IDENTITY_FIELDS = frozenset({"team"})
#: Fields ``--check-wiring`` does not compare: how the value was produced, not what the consumer enforces.
_PROVENANCE_FIELDS = frozenset({"agentteams_version", "model_substrate_wasm_source", "os_sandbox_reason",
                                "wasm_sandbox_reason"})
_ON_OFF = ("on", "off")
_SOURCES = ("brief", "cli")
#: ``SandboxState.os_reason`` / ``wasm_reason`` vocabulary (``<p>`` / ``<tok>`` are slugs / host-feature tokens).
_REASON_CHARS = frozenset(string.ascii_lowercase + string.digits + "-:")
_MAX_REASON = 64
_MAX_TEXT = 4096


class ProjectionError(ValueError):
    """A projection that cannot be written or read safely (symlink, malformed file, team outside the project)."""


@dataclass
class ProjectionOutcome:
    """What :func:`write_projection` did.

    Attributes:
        status: ``"written"`` | ``"unchanged"`` | ``"kept-foreign"`` | ``"dry-run"``.
        path: The projected file.
        notices: Lines for the operator (already formatted).
    """

    status: str
    path: Path
    notices: list[str] = field(default_factory=list)


def _team_rel(team_dir: Path, project_root: Path) -> str:
    root, team = Path(project_root).resolve(), Path(team_dir).resolve()
    if not team.is_relative_to(root):
        raise ProjectionError(f"the team directory {team_dir} is outside the project root; nothing is projected")
    rel = team.relative_to(root).as_posix()
    return rel if rel else "."


def projection_record(manifest: dict[str, Any], *, team: str) -> dict[str, Any] | None:
    """Return the D2 record for ``manifest``, or None when the switch is not projected.

    Projected only when ``--wasm-substrate`` or the brief's ``wasm_sandbox.model_substrate`` sets it (a *kept* floor
    is never re-projected: the file it came from is left as it is).

    Args:
        manifest: The team manifest (the transient CLI keys and the CLI-set project root).
        team: The project-relative agents dir of this render (``".claude/agents"``).

    Returns:
        The record, fields in :data:`FIELDS` order, or None.

    Raises:
        ValueError: A malformed switch value or ``wasm_sandbox`` block (as ``effective_sandbox_state``).
    """
    from agentteams import __version__
    from agentteams.sandbox_state import effective_sandbox_state
    from agentteams.wasm_config import substrate_switch

    value, source = substrate_switch(manifest)
    if value is None or source not in _SOURCES:
        return None
    state = effective_sandbox_state(manifest)
    record = {
        "schema": SCHEMA_ID, "producer": PRODUCER, "agentteams_version": str(__version__), "team": team,
        "os_sandbox": "on" if state.os else "off", "os_sandbox_reason": state.os_reason,
        "wasm_sandbox": "on" if state.wasm else "off", "wasm_sandbox_reason": state.wasm_reason,
        "model_substrate_wasm": value, "model_substrate_wasm_source": source, "require_enforced": True,
    }
    return validate_record(record)


def _is_reason(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= _MAX_REASON and set(value) <= _REASON_CHARS
            and not value.startswith(("-", ":")))


def _is_team(value: object) -> bool:
    if not isinstance(value, str) or not 0 < len(value) <= 256 or value.startswith("/") or "\\" in value:
        return False
    return value == "." or all(seg not in ("", ".", "..") and all(c.isalnum() or c in "._-" for c in seg)
                               for seg in value.split("/"))


def validate_record(obj: object) -> dict[str, Any]:
    """Return ``obj`` when it has exactly the D2 shape (the stdlib twin of the JSON schema), else raise.

    Args:
        obj: A decoded record.

    Returns:
        ``obj`` (unchanged).

    Raises:
        ProjectionError: Any missing or extra key, or a value outside its enum / grammar.
    """
    if not isinstance(obj, dict) or set(obj) != set(FIELDS):
        raise ProjectionError(f"{PROJECTION_REL} must have exactly the keys {', '.join(FIELDS)}")
    checks = {
        "schema": obj["schema"] == SCHEMA_ID, "producer": obj["producer"] == PRODUCER,
        "agentteams_version": isinstance(obj["agentteams_version"], str) and 0 < len(obj["agentteams_version"]) <= 64,
        "team": _is_team(obj["team"]),
        "os_sandbox": obj["os_sandbox"] in _ON_OFF, "os_sandbox_reason": _is_reason(obj["os_sandbox_reason"]),
        "wasm_sandbox": obj["wasm_sandbox"] in _ON_OFF, "wasm_sandbox_reason": _is_reason(obj["wasm_sandbox_reason"]),
        "model_substrate_wasm": obj["model_substrate_wasm"] in _ON_OFF,
        "model_substrate_wasm_source": obj["model_substrate_wasm_source"] in _SOURCES,
        "require_enforced": obj["require_enforced"] is True,
    }
    bad = [k for k in FIELDS if not checks[k]]
    if bad:
        raise ProjectionError(f"{PROJECTION_REL}: invalid {', '.join(bad)}")
    return obj


def projection_text(record: dict[str, Any]) -> str:
    """Return the canonical file text of ``record`` (two-space JSON in :data:`FIELDS` order, one trailing newline).

    Args:
        record: A record that passes :func:`validate_record`.

    Returns:
        The text.

    Raises:
        ProjectionError: The record is malformed.
    """
    validate_record(record)
    return json.dumps({k: record[k] for k in FIELDS}, indent=2) + "\n"


def _path(project_root: Path) -> Path:
    return Path(project_root) / PROJECTION_REL


def _check_no_follow(project_root: Path) -> None:
    """Refuse a symlinked / non-directory ``.agentteams`` or a symlinked / non-regular projection file."""
    path = _path(project_root)
    for p, want_dir in ((path.parent, True), (path, False)):
        if not os.path.lexists(p):
            return
        mode = os.lstat(p).st_mode
        if (want_dir and not stat.S_ISDIR(mode)) or (not want_dir and not stat.S_ISREG(mode)):
            kind = "a real directory" if want_dir else "a regular file"
            raise ProjectionError(f"{p} is a symlink or not {kind}; the model-substrate switch is not projected")


def preflight(project_root: Path) -> None:
    """Refuse a render whose projection could not be written safely, before anything is emitted (fail closed).

    Args:
        project_root: The project root.

    Raises:
        ProjectionError: ``.agentteams`` is a symlink or not a directory, or the file is a symlink / non-regular.
    """
    _check_no_follow(project_root)


def _read_raw(project_root: Path) -> object | None:
    """The decoded file, None when absent; raise ProjectionError when it cannot be read safely or is not JSON."""
    path = _path(project_root)
    _check_no_follow(project_root)
    if not os.path.lexists(path):
        return None
    from agentteams.signing_switch import _read_regular_text

    try:
        text = _read_regular_text(path)
    except ValueError as exc:
        raise ProjectionError(str(exc)) from exc
    if len(text) > _MAX_TEXT:
        raise ProjectionError(f"{path} is larger than {_MAX_TEXT} bytes")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ProjectionError(f"{path} is not valid JSON: {exc}") from exc


def read_projection(project_root: Path) -> dict[str, Any] | None:
    """Read and exact-shape check the projected file (the reference reader a consumer should mirror).

    Args:
        project_root: The project root.

    Returns:
        The record, or None when the file is absent (no projection).

    Raises:
        ProjectionError: A symlink, an unreadable or oversized file, invalid JSON, or any shape violation
            (wrong ``schema`` / ``producer``, ``require_enforced`` not true, an extra or missing key). A consumer
            must then refuse its WASM-switch-dependent runs rather than guess.
    """
    raw = _read_raw(project_root)
    return None if raw is None else validate_record(raw)


def kept_value(project_root: Path) -> str | None:
    """Return the floor an existing file holds when the switch is unset: None (absent), else ``"on"`` / ``"off"``.

    The same rule as the gate's previous-state evidence (``sandbox_state.previous_sandbox_state``): only a
    readable JSON object saying ``model_substrate_wasm: "off"`` is ``"off"``; anything else present is ``"on"``.

    Args:
        project_root: The project root.

    Returns:
        The kept value, or None.

    Raises:
        Nothing.
    """
    if not os.path.lexists(_path(project_root)):
        return None
    try:
        raw = _read_raw(project_root)
    except ProjectionError:
        return "on"
    return "off" if isinstance(raw, dict) and raw.get("model_substrate_wasm") == "off" else "on"


def write_projection(project_root: Path, record: dict[str, Any], *, dry_run: bool) -> ProjectionOutcome:
    """Write ``record`` to ``<project_root>/.agentteams/sandbox-state.json`` under the ownership rules.

    Args:
        project_root: The project root.
        record: The record (:func:`projection_record`).
        dry_run: Decide and report only; write nothing.

    Returns:
        The outcome.

    Raises:
        ProjectionError: A malformed record, or a symlinked / non-regular ``.agentteams`` or file.
        OSError: The write failed (the temp file is removed).
    """
    path = _path(project_root)
    text = projection_text(record)
    outcome = ProjectionOutcome(status="written", path=path)
    _check_no_follow(project_root)
    try:
        current = _read_raw(project_root)
    except ProjectionError:
        current = "unreadable"  # a regular file in a real dir that is not JSON: ownership unclear, so it is kept
    if current is not None and (not isinstance(current, dict) or current.get("producer") != PRODUCER):
        outcome.status = "kept-foreign"
        outcome.notices.append(
            f"  ⚠  SANDBOX STATE KEPT: {PROJECTION_REL} is not agentteams-owned (or unreadable); left untouched, so "
            f"the model-substrate switch ({record['model_substrate_wasm']}) is NOT projected. Remove or fix the file "
            "and re-run to project it.")
        return outcome
    if current is not None:
        changed = [k for k in FIELDS if k not in _IDENTITY_FIELDS and current.get(k) != record[k]]
        if not changed and set(current) == set(FIELDS):
            outcome.status = "unchanged"
            return outcome
        if current.get("team") != record["team"]:
            outcome.notices += [f"  ⚠  SANDBOX STATE CHANGED by {record['team']}: {k} {current.get(k)!r} -> "
                                f"{record[k]!r} (last written by {current.get('team')!r})" for k in changed
                                if k != "agentteams_version"]
    if dry_run:
        outcome.status = "dry-run"
        return outcome
    from agentteams.signing_switch import _replace_no_follow

    _replace_no_follow(path, text)
    return outcome


def emit_projection(manifest: dict[str, Any], project_root: Path, team_dir: Path, *,
                    dry_run: bool = False) -> ProjectionOutcome | None:
    """The generate/sync call site: write the projection when set, or say that an unset switch left the file.

    Best-effort like the sibling project-root artifact writers: a failure is printed (stderr), never raised into the
    build; the render was already preflighted (:func:`preflight`) and ``--check-wiring`` / ``--verify-integrity``
    report a missing or differing file.

    Args:
        manifest: The team manifest.
        project_root: The project root.
        team_dir: This render's agents dir.
        dry_run: Write nothing.

    Returns:
        The outcome, or None when nothing is projected.

    Raises:
        Nothing.
    """
    from agentteams.wasm_config import substrate_switch

    try:
        value, source = substrate_switch(manifest)
        if source == "kept":
            print(f"  ·  SANDBOX STATE KEPT: no brief or argv sets wasm_sandbox.model_substrate; {PROJECTION_REL} "
                  f"(model_substrate_wasm {value}) stays as the consumer's floor. Lowering it to \"off\" or deleting "
                  "the file is constraint-relaxing (sandbox-wasm-off:model-substrate).")
            return None
        record = projection_record(manifest, team=_team_rel(team_dir, project_root))
        if record is None:
            return None
        outcome = write_projection(project_root, record, dry_run=dry_run)
    except (ValueError, OSError) as exc:
        print(f"  !  SANDBOX STATE NOT WRITTEN: {exc}", file=sys.stderr)
        return None
    for line in outcome.notices:
        print(line, file=sys.stderr)
    if outcome.status == "written":
        print(f"  ✓  Projected the model-substrate WASM switch ({value}, {source}) to {PROJECTION_REL} "
              "(a floor for consumers that run model code; independent of wasm_sandbox.enabled).")
    return outcome


def wiring_lines(manifest: dict[str, Any], project_root: Path, team_dir: Path) -> tuple[bool, list[str]]:
    """Return the ``--check-wiring`` lines (P20 D8): exists / parses / matches the render, booleans only.

    Args:
        manifest: The team manifest (with the CLI keys and project root set).
        project_root: The project root.
        team_dir: The agents dir checked.

    Returns:
        ``(ok, lines)``; ``(True, [])`` when the manifest projects nothing.

    Raises:
        Nothing.
    """
    try:
        record = projection_record(manifest, team=_team_rel(team_dir, project_root))
    except ValueError as exc:
        return False, [f"ERROR: model-substrate projection: {exc}"]
    if record is None:
        return True, []
    exists = os.path.lexists(_path(project_root))
    try:
        current = read_projection(project_root)
        parses = current is not None
    except ProjectionError:
        current, parses = None, False
    compared = [k for k in FIELDS if k not in _IDENTITY_FIELDS | _PROVENANCE_FIELDS]
    matches = bool(parses and current is not None and all(current[k] == record[k] for k in compared))
    line = (f"model-substrate projection {PROJECTION_REL}: exists={str(exists).lower()} "
            f"parses={str(parses).lower()} matches={str(matches).lower()}")
    return matches, [line if matches else f"ERROR: {line} (re-run generate; a consumer refuses a malformed file)"]


def verify_projection(team_dir: Path, project_root: Path) -> tuple[int, list[str]]:
    """Compare the projected file with the last render's build-log ``sandbox_state`` (``--verify-integrity``).

    A file that projects a lower floor than the record (record ``on``; file absent, unreadable, malformed or
    ``off``) is a finding (exit 1): someone lowered the floor outside the gate. Any other difference is advisory.

    Args:
        team_dir: The team dir (``references/build-log.json``).
        project_root: The project root.

    Returns:
        ``(rc, lines)``: rc 1 on a lowered floor, else 0; no lines when the record projects nothing.

    Raises:
        Nothing.
    """
    from agentteams.sandbox_state import verifier_sandbox_state

    try:
        recorded = verifier_sandbox_state(None, team_dir, project_root)
    except (ValueError, ImportError):
        recorded = None
    expected = recorded.model_substrate if recorded is not None else None
    if expected is None:
        return 0, []
    try:
        current = read_projection(project_root)
        why = "absent" if current is None else ""
    except ProjectionError as exc:
        current, why = None, f"unusable ({exc})"
    got = current["model_substrate_wasm"] if current is not None else None
    if expected == "on" and got != "on":
        return 1, [f"  [LOWERED] {PROJECTION_REL}: the last render projected model_substrate_wasm on; the file "
                   f"{'is ' + why if why else 'says ' + repr(got)} (re-run generate, or record the relaxation)"]
    if got != expected:
        return 0, [f"  [MODIFIED] {PROJECTION_REL}: recorded {expected}, file {got or why} (review)"]
    return 0, [f"Model-substrate projection: OK ({PROJECTION_REL} = {got})"]


def project_root_for(team_dir: Path) -> Path:
    """Return the project root of a team dir: strip a default agents-dir suffix, else the dir itself.

    Args:
        team_dir: A team's agents dir (or a project root).

    Returns:
        The project root.

    Raises:
        Nothing.
    """
    from agentteams.frameworks._sandbox_emit import TEAM_KEY_BY_FRAMEWORK, team_agents_dir

    resolved = Path(team_dir).resolve()
    for key in dict.fromkeys(TEAM_KEY_BY_FRAMEWORK.values()):
        sub = team_agents_dir(key)
        if resolved.as_posix().endswith("/" + sub):
            return resolved.parents[sub.count("/")]
    return resolved


__all__ = [
    "FIELDS",
    "PRODUCER",
    "PROJECTION_REL",
    "SCHEMA_ID",
    "ProjectionError",
    "ProjectionOutcome",
    "emit_projection",
    "kept_value",
    "preflight",
    "project_root_for",
    "projection_record",
    "projection_text",
    "read_projection",
    "validate_record",
    "verify_projection",
    "wiring_lines",
]
