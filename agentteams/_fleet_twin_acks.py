"""_fleet_twin_acks.py — fleet flags a new or changed fence-twin acknowledgement for operator review (2026-10-11).

``references/fence-twin-acks.csv`` (``agentteams.fence_twin_acks``) turns a kept twin's fleet REVIEW into a
``twin kept (acknowledged)`` notice. Nothing ties a row to the operator: anyone who can write the team's
files can add one, and the same fleet run that first renders with it would report the twin as settled.
The operator decision (2026-10-11) is that ledger changes are flagged for operator review.

**The recorded state.** After an applied run, fleet records, per workspace and target, the
acknowledged-twin notices it showed the operator, in ``<run dir>/fence-twin-acks-seen.json`` beside that
run's ``report.json`` (under ``.agentteams-fleet/`` or ``--fleet-report``). The next applied run reads the
record of the latest earlier run directory that has one. An
acknowledged notice that is not in that record is a REVIEW reason
(:data:`REVIEW_REASON`). A new row, a changed reason, or a twin re-acknowledged after an edit
changes the notice, so each of them is flagged once, whether or not it was committed.

**Fail-safe.** The record is keyed by the sha256 of the whole notice line (file, heading, fence, digest
and reason), so only an identical acknowledgement counts as seen. A missing, unreadable or malformed
record counts as empty: every acknowledgement is flagged again (so does a ``--fleet-report`` directory
with no earlier run in it). So does a record that is, or sits in, a symlink, or one inside a git work tree:
the default report dir is inside the parent, and when the parent is itself a repository its agents could
plant a record there. A target's notices are recorded only when its row is OK, or REVIEW with no fence
imbalance and :data:`REVIEW_REASON` in its detail. A FAIL, a fence-imbalance REVIEW (whose detail names
only that) or a target that did not run leaves its old record, so a flag that was not shown is shown next
time. A record that cannot be written prints a warning; the next run flags again.

**Scope.** The record says "fleet showed this to the operator", not "the operator approved it": the
REVIEW is shown once, and an operator who rejects the row deletes it. Anyone who can write the record
file outside a repository can still pre-seed it, and an agent that can run ``agentteams --fleet --yes``
itself can clear the flag by running fleet twice; tying a row to the operator would take an operator
signature. The record lives under the operator's fleet output directory, which fleet never discovers or
updates. Keying on the whole line means a release that rewords a kept-twin reason re-flags once (safe). A non-git workspace is not classified at all (no twin of any kind is REVIEW there), so this
check, like the kept-twin one, covers git workspaces only. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from agentteams.atomicio import _atomic_write_text
from agentteams.git_exec import run_git

if TYPE_CHECKING:
    from agentteams.fleet import TargetResult
from agentteams.fence_twins import ACKED_PREFIX, DROPPED_PREFIX, KEPT_PREFIX, RETIRED_MARK

#: The record, in each applied run's report directory (beside ``report.json``).
SEEN_FILE = "fence-twin-acks-seen.json"
#: A run directory's name (``fleet.run_fleet``'s run id: UTC ``YYYYMMDD-HHMMSS``).
_RUN_ID_RE = re.compile(r"^\d{8}-\d{6}$")
#: The REVIEW detail fragment for an acknowledgement the operator has not been shown.
REVIEW_REASON = "fence-twin acknowledgement new or changed since the last fleet run"
_VERSION = 1

Seen = dict[str, dict[str, list[str]]]


def _key(line: str) -> str:
    """The record key of one acknowledged-twin notice: sha256 of the stripped line."""
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()


def twin_signals(update_output: str) -> dict[str, list[str]]:
    """Split an update's output into its pre-fencing twin notices.

    Args:
        update_output: The captured stdout/stderr of one target's update.

    Returns:
        ``twins_kept``, ``twins_dropped``, ``twins_acknowledged`` and ``twins_retired`` (drops that
        retired a rule the current template no longer carries), each a list of stripped lines.

    Raises:
        Nothing.
    """
    lines = [line.strip() for line in update_output.splitlines()]
    return {
        "twins_kept": [line for line in lines if f"{KEPT_PREFIX}:" in line],
        "twins_dropped": [line for line in lines if f"{DROPPED_PREFIX}:" in line],
        "twins_acknowledged": [line for line in lines if f"{ACKED_PREFIX}:" in line],
        "twins_retired": [line for line in lines if f"{DROPPED_PREFIX}:" in line and RETIRED_MARK in line],
    }


def _latest_record(report_root: Path) -> Path | None:
    """The record of the latest run directory beside *report_root*, older than it, that has one."""
    try:
        runs = sorted((d for d in report_root.parent.iterdir()
                       if _RUN_ID_RE.match(d.name) and d.name < report_root.name), reverse=True)
    except OSError:
        return None
    return next((d / SEEN_FILE for d in runs if (d / SEEN_FILE).exists() or (d / SEEN_FILE).is_symlink()), None)


def _untrusted(path: Path) -> str:
    """Why the record at *path* cannot be trusted, or "" when it can."""
    if any(p.is_symlink() for p in (path, path.parent, path.parent.parent)):
        return "it is, or sits in, a symlink"
    inside = run_git(path.parent, "rev-parse", "--is-inside-work-tree", hardened=True,
                     env={**os.environ, "LC_ALL": "C"})   # English stderr for the check below
    if inside.returncode == 0 and inside.stdout.strip() == "true":
        return ("it is inside a git work tree, where a workspace's agents could plant it "
                "(pass --fleet-report outside every repository)")
    if inside.returncode != 0 and "not a git repository" not in inside.stderr:   # e.g. dubious ownership
        return f"git could not tell whether it is inside a repository ({inside.stderr.strip()[:120]})"
    return ""


def load_seen(report_root: Path) -> Seen:
    """Read the record of acknowledgements earlier fleet runs showed the operator.

    Args:
        report_root: This run's report directory (``<fleet report dir>/<run id>``).

    Returns:
        ``{workspace: {target: [key, ...]}}`` from the latest earlier run's record. Empty when there is
        none, or when it is unreadable or malformed (with a warning), so every acknowledgement is flagged.

    Raises:
        Nothing.
    """
    try:
        path = _latest_record(Path(report_root))
        why = _untrusted(path) if path is not None else ""
    except (OSError, subprocess.SubprocessError, ValueError) as exc:   # in doubt: flag everything
        path, why = Path(report_root).parent, f"cannot check it ({exc})"
    if path is None:
        return {}
    if why:
        print(f"  WARNING: {path}: not used ({why}); every fence-twin acknowledgement is flagged for review",
              file=sys.stderr)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        seen = data["workspaces"]
        if data.get("version") != _VERSION or not isinstance(seen, dict) or not all(
                isinstance(targets, dict) and all(
                    isinstance(keys, list) and all(isinstance(k, str) for k in keys) for keys in targets.values())
                for targets in seen.values()):
            raise ValueError("unexpected shape")
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        print(f"  WARNING: {path}: unreadable ({exc}); every fence-twin acknowledgement is flagged for review",
              file=sys.stderr)
        return {}
    return seen


def unreviewed(seen: Mapping[str, Mapping[str, list[str]]], workspace: str, target: str,
               acknowledged: Iterable[str]) -> list[str]:
    """The acknowledged-twin notices this target has not shown the operator before.

    Args:
        seen: The record from :func:`load_seen`.
        workspace: The workspace path, as ``TargetResult.workspace`` holds it.
        target: The fleet target (``github``, ``claude-direct``, ...).
        acknowledged: This run's ``twins_acknowledged`` notices for the target.

    Returns:
        The notices whose key is not in the record, in order.

    Raises:
        Nothing.
    """
    known = set(seen.get(workspace, {}).get(target, ()))
    return [line for line in acknowledged if _key(line) not in known]


def twin_reasons(cls: Mapping[str, list[str]], unreviewed_acks: list[str]) -> list[str]:
    """The twin-related REVIEW detail fragments for one target ("" where a signal is absent).

    Args:
        cls: ``fleet._classify`` output (its :func:`twin_signals` keys).
        unreviewed_acks: The target's :func:`unreviewed` notices.

    Returns:
        Fragments for kept twins, dropped twins that retired a rule, and unreviewed acknowledgements.

    Raises:
        Nothing.
    """
    return [
        f"unfenced pre-fencing twin kept ({len(cls['twins_kept'])}) — resolve by hand" if cls["twins_kept"] else "",
        f"dropped twin retired a rule ({len(cls['twins_retired'])}) — confirm in the .lost sidecar"
        if cls["twins_retired"] else "",
        f"{REVIEW_REASON} ({len(unreviewed_acks)}) — needs operator review: see twins_ack_unreviewed"
        if unreviewed_acks else "",
    ]


def record_seen(report_root: Path, targets: Iterable[TargetResult], seen: Seen) -> None:
    """Record the acknowledged-twin notices this applied run showed the operator.

    A target with unreviewed notices is recorded only when its row is REVIEW with no fence imbalance (that
    branch's detail names only the imbalance, and holds file names a writer controls) and
    :data:`REVIEW_REASON` in its detail; any other target only when it is OK or REVIEW. Otherwise, and for
    targets that did not run, the old entry stays.

    Args:
        report_root: This run's report directory; the record is written there.
        targets: The run's ``fleet.TargetResult`` rows.
        seen: The record :func:`load_seen` returned at the start of the run (updated in place).

    Returns:
        None.

    Raises:
        Nothing: a write failure prints a warning (the next run flags those acknowledgements again).
    """
    for tr in targets:
        if tr.status not in ("OK", "REVIEW"):
            continue
        if tr.twins_ack_unreviewed and (tr.status != "REVIEW" or tr.fence_imbalances
                                        or REVIEW_REASON not in tr.detail):
            continue
        seen.setdefault(tr.workspace, {})[tr.target] = sorted({_key(line) for line in tr.twins_acknowledged})
    path = Path(report_root) / SEEN_FILE
    try:
        _atomic_write_text(path, json.dumps({"version": _VERSION, "workspaces": seen}, indent=2, sort_keys=True)
                           + "\n")
    except OSError as exc:
        print(f"  WARNING: {path}: not written ({exc}); the next run flags these acknowledgements again",
              file=sys.stderr)


__all__ = ["REVIEW_REASON", "SEEN_FILE", "load_seen", "record_seen", "twin_reasons", "twin_signals", "unreviewed"]
