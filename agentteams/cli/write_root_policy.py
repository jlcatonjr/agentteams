"""Operator checkpoint for sandbox write roots: provenance, widening notice, external acceptance.

The pinned emitters refuse hard-banned roots (``frameworks/_write_roots.py``). This module is
the generation-time half (follow-up #2, ``brief-json-privilege-tamper``, 2026-09-30):

* every root not already in the protected live baseline is printed as a ``SANDBOX WIDENING``
  line naming its source (brief, coordination, signed grant), so a brief edited by an agent is
  visible at the operator's next ``--update``;
* a NEW root outside the project needs ``--accept-write-root PATH`` on the operator's own argv.
  The baseline is read only from the live ``.claude/settings.json`` (protected by the Claude
  sandbox and, since this change, by ``confine-run.sh``); for other frameworks it is empty.

HONEST LIMIT: an agent can plant ``--accept-write-root`` in a script the operator later runs
(a Makefile, a task, an "operator script"). The widening notice names every accepted external
root and where it came from, so it is visible, not prevented.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Iterable

from agentteams.frameworks._write_roots import (
    FORBIDDEN_ACCEPTANCE_KEYS,
    PROJECT_ROOT_KEY,
    PROVENANCE_KEY,
    is_external_root,
    normalize_root,
    unaccepted_external_roots,
    validate_write_roots,
)


class WriteRootPolicyError(RuntimeError):
    """A write root is refused, or an external root was not accepted by the operator."""


def live_claude_allow_write(project_root: Path) -> list[str]:
    """Return ``sandbox.filesystem.allowWrite`` of the live ``.claude/settings.json`` (else ``[]``).

    Only ``settings.json`` counts: ``settings.local.json`` is not an accepted baseline.

    Args:
        project_root: The project root.

    Returns:
        The live allowWrite strings, or an empty list when absent or unreadable.
    """
    try:
        data = json.loads((project_root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sandbox = data.get("sandbox") if isinstance(data, dict) else None
    fs = sandbox.get("filesystem") if isinstance(sandbox, dict) else None
    allow = fs.get("allowWrite") if isinstance(fs, dict) else None
    return [r for r in allow if isinstance(r, str) and r] if isinstance(allow, list) else []


def begin(manifest: dict[str, Any], project_root: Path) -> list[str]:
    """Strip input-supplied transient keys, set the project root, and snapshot the brief roots.

    Call before any union (grants, coordination) so provenance can be attributed.

    Args:
        manifest: The team manifest (mutated).
        project_root: The project root.

    Returns:
        The brief's own ``workspace_write_roots`` (a copy).

    Raises:
        WriteRootPolicyError: The brief or manifest carries an acceptance key.
    """
    present = [k for k in FORBIDDEN_ACCEPTANCE_KEYS if k in manifest]
    if present:
        raise WriteRootPolicyError(
            f"the brief/manifest carries {', '.join(present)}: write-root acceptance is operator "
            "argv only (--accept-write-root). Remove the key."
        )
    manifest.pop(PROVENANCE_KEY, None)
    manifest[PROJECT_ROOT_KEY] = Path(project_root).resolve()
    return list(manifest.get("workspace_write_roots") or [])


def enforce(
    manifest: dict[str, Any],
    *,
    framework_id: str,
    brief_roots: Iterable[str],
    grant_roots: Iterable[str],
    coordination_roots: Iterable[str],
    accepted: Iterable[str] | None,
    confined: bool,
) -> None:
    """Validate the final roots, record provenance, print widenings, and require acceptance.

    Args:
        manifest: The team manifest after every union (mutated: provenance key).
        framework_id: The target framework (claude reads the live baseline).
        brief_roots: Roots from the brief's ``workspace_write_roots``.
        grant_roots: Roots added by Ed25519-verified grants (exempt from acceptance only).
        coordination_roots: Roots added from ``coordination_write_roots``.
        accepted: ``--accept-write-root`` values (operator argv).
        confined: Whether any confinement consumes the roots (inert otherwise).

    Raises:
        WriteRootPolicyError: A root is hard-banned, or an external root is unaccepted.
    """
    if not confined:
        return
    roots = list(manifest.get("workspace_write_roots") or ["."])
    try:
        validate_write_roots(roots, manifest)
    except ValueError as exc:
        raise WriteRootPolicyError(str(exc)) from exc
    source = {r: "brief workspace_write_roots" for r in brief_roots}
    source.update({r: "brief coordination_write_roots (unsigned)" for r in coordination_roots})
    source.update({r: "signed capability grant" for r in grant_roots})
    manifest[PROVENANCE_KEY] = tuple((r, source.get(r, "default")) for r in roots)
    project_root = str(manifest[PROJECT_ROOT_KEY])
    baseline = live_claude_allow_write(Path(project_root)) if framework_id == "claude" else []
    accepted_list = list(accepted or [])
    known = {normalize_root(b) for b in baseline}
    for r in roots:
        if r == ".":
            continue
        external = is_external_root(r, project_root)
        if normalize_root(r) in known:
            if external:  # always visible: a planted baseline must not silence an external root
                print(f"  ·  sandbox allowWrite keeps external root {r!r} (already in the live "
                      ".claude/settings.json allowWrite).", file=sys.stderr)
            continue
        hidden = external and (r.startswith("~/.") or os.path.normpath(os.path.expanduser(r)).startswith(
            os.path.join(os.path.expanduser("~"), ".")))
        print(
            f"  !  SANDBOX WIDENING: allowWrite gains {r!r} (source: {source.get(r, 'default')}"
            f"{'; EXTERNAL' if external else ''}{'; a hidden home dir' if hidden else ''}). "
            "Review before re-merging; the brief is agent-writable.",
            file=sys.stderr,
        )
    need = unaccepted_external_roots(roots, project_root=project_root, baseline=baseline,
                                     accepted=accepted_list, signed=list(grant_roots))
    if need:
        named = "; ".join(f"{r!r} (source: {source.get(r, 'default')})" for r in need)
        raise WriteRootPolicyError(
            f"new external sandbox write root(s) need operator acceptance: {named}. If you intend "
            "this, re-run with " + " ".join(f"--accept-write-root {shlex.quote(r)}" for r in need)
            + " (operator argv only; never from a brief, env var or file)."
        )
