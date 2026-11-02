"""Operator checkpoint for sandbox write roots: provenance, widening notice, external acceptance.

The pinned emitters refuse hard-banned roots (``frameworks/_write_roots.py``). This module is
the generation-time half (follow-up #2, ``brief-json-privilege-tamper``, 2026-09-30):

* every root not already in the protected live baseline is printed as a ``SANDBOX WIDENING``
  line naming its source (brief, coordination, signed grant), so a brief edited by an agent is
  visible at the operator's next ``--update``;
* a NEW root outside the project needs ``--accept-write-root PATH`` on the operator's own argv.
  The baseline is read only from the live ``.claude/settings.json`` (protected by the Claude
  sandbox and, since this change, by ``confine-run.sh``); for other frameworks it is empty.

:func:`warn_sandbox_relaxation` prints one ``SANDBOX RELAXATION:`` line per sandbox relaxation a render makes
(OS or WASM sandbox off, a widened WASM grant, native exec kept, a runner program moved off WASM; D7), on every
framework. It also prints ``SANDBOX RELAXATION: prompt-root protection removed`` when the live
``.claude/settings.json`` ``permissions.deny`` carries prompt-root ``Edit`` rules
(``protect_prompt_roots``, follow-up #8 phase 2) that the new render drops: warn only.

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
from typing import TYPE_CHECKING, Any, Iterable

from agentteams.frameworks._prompt_root_protect import (
    ALL_PROMPT_ROOT_EDIT_RULES,
    prompt_root_edit_rules,
    prompt_roots_enabled,
)
from agentteams.frameworks._write_roots import (
    FORBIDDEN_ACCEPTANCE_KEYS,
    PROJECT_ROOT_KEY,
    PROVENANCE_KEY,
    is_external_root,
    normalize_root,
    unaccepted_external_roots,
    validate_write_roots,
)

if TYPE_CHECKING:
    from agentteams.sandbox_state import SandboxExceptions, SandboxState

#: The prefix every sandbox-relaxation notice starts with, so a reviewer (or fleet's REVIEW classifier) keys on one
#: string.
SANDBOX_RELAXATION_PREFIX = "  !  SANDBOX RELAXATION:"


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


def live_claude_permission_deny(project_root: Path) -> list[str]:
    """Return ``permissions.deny`` of the live ``.claude/settings.json`` (else ``[]``).

    Args:
        project_root: The project root.

    Returns:
        The live deny rule strings, or an empty list when absent or unreadable.
    """
    try:
        data = json.loads((project_root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    perms = data.get("permissions") if isinstance(data, dict) else None
    deny = perms.get("deny") if isinstance(perms, dict) else None
    return [r for r in deny if isinstance(r, str)] if isinstance(deny, list) else []


def warn_prompt_root_relaxation(manifest: dict[str, Any], *, framework_id: str, confined: bool) -> list[str]:
    """Print ``SANDBOX RELAXATION`` when the live deny has prompt-root rules the new render drops.

    Fires when ``protect_prompt_roots`` was turned off or removed from the (agent-writable) brief,
    or the sandbox itself was turned off. Warn only: visible, not prevented.

    Args:
        manifest: The team manifest (reads ``protect_prompt_roots`` and the project root).
        framework_id: The target framework (only claude renders ``.claude/settings.json``).
        confined: Whether the new render carries the sandbox block (and so any deny rules).

    Returns:
        The dropped rules (empty when nothing is dropped).
    """
    if framework_id != "claude" or not isinstance(manifest.get(PROJECT_ROOT_KEY), Path):
        return []
    root = manifest[PROJECT_ROOT_KEY]
    if root.parts[-2:] == (".claude", "agents"):  # `--output <project>/.claude/agents`
        root = root.parents[1]
    project_root = str(root)
    live = [r for r in live_claude_permission_deny(root) if r in ALL_PROMPT_ROOT_EDIT_RULES]
    kept = set(prompt_root_edit_rules(project_root)) if confined and prompt_roots_enabled(manifest) else set()
    dropped = [r for r in dict.fromkeys(live) if r not in kept]
    if dropped:
        print(
            "  !  SANDBOX RELAXATION: prompt-root protection removed: the live .claude/settings.json "
            f"permissions.deny carries {', '.join(dropped)}, which the new render drops "
            "(protect_prompt_roots is off or absent in the brief, or the sandbox is off). Review "
            "before re-merging; the brief is agent-writable.",
            file=sys.stderr,
        )
    return dropped


def warn_sandbox_relaxation(old_state: SandboxState | None, new_state: SandboxState, *, framework_id: str,
                            exceptions: tuple[SandboxExceptions | None, SandboxExceptions] | None = None,
                            profile_would_emit: bool = True) -> list[str]:
    """Print one ``SANDBOX RELAXATION`` line per relaxation ``new_state`` makes over ``old_state`` (D7; all frameworks).

    The notice is what an ungoverned workspace gets instead of a signature check, and what a governed one gets beside
    it. Only new relaxations print; one that continues from an earlier render is the gate's to report.

    Args:
        old_state: The previous state, or None (a first render, or never confined).
        new_state: The state this render emits.
        framework_id: The target framework.
        exceptions: ``(previous, new)`` native-exec keep / runner program inputs, or None.
        profile_would_emit: Whether the privilege profile would emit an OS boundary were it not switched off
            (the gate's own input; P00 passes it so the notice and the gate agree on a first render).

    Returns:
        The printed lines (each starts with :data:`SANDBOX_RELAXATION_PREFIX`).

    Raises:
        ValueError: A malformed slug or grant mount in either state.
    """
    from agentteams.cli.sandbox_effects import (  # local: keep the gate out of this module's import graph
        NATIVE_EXEC_KEEP,
        SANDBOX_OS_OFF,
        SANDBOX_WASM_OFF,
        WASM_GRANT_WIDEN,
        WASM_TO_OS_SWITCH,
    )
    from agentteams.cli.sandbox_relaxation_gate import plan_relaxations

    planned = plan_relaxations(old_state, new_state, framework_id=framework_id,
                               profile_would_emit=profile_would_emit, exceptions=exceptions)
    lines: list[str] = []
    for r in (p for p in planned if not p.continuing):
        if r.kind == SANDBOX_OS_OFF and old_state is not None:
            lines.append(f"os sandbox OFF for {framework_id} (reason: {new_state.os_reason}); it was on. No OS "
                         "boundary is emitted; a live block you merged earlier still applies until you remove it "
                         "(run --check-wiring).")
        elif r.kind == SANDBOX_OS_OFF:
            lines.append(f"os sandbox OFF for {framework_id} by {new_state.os_reason}; the profile would emit one.")
        elif r.kind == SANDBOX_WASM_OFF and r.qualifier == framework_id:
            lines.append(f"wasm sandbox OFF (reason: {new_state.wasm_reason}); it was on.")
        elif r.kind == SANDBOX_WASM_OFF:
            lines.append("model-substrate WASM switch OFF; it was on.")
        elif r.kind == WASM_GRANT_WIDEN:
            lines += [f"wasm grant widened: +{token}" for token in r.grants]
        elif r.kind == NATIVE_EXEC_KEEP:
            lines += [f"native exec kept for {token.split(':', 1)[1]} while wasm is on" for token in r.grants]
        elif r.kind == WASM_TO_OS_SWITCH:
            lines.append(f"runner program for {r.qualifier} moved from sandbox wasm to os")
    lines = [f"{SANDBOX_RELAXATION_PREFIX} {line}" for line in lines]
    for line in lines:
        print(line, file=sys.stderr)
    return lines


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
    sandbox_states: tuple[SandboxState | None, SandboxState] | None = None,
    sandbox_exceptions: tuple[SandboxExceptions | None, SandboxExceptions] | None = None,
    sandbox_profile_would_emit: bool = True,
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
        sandbox_states: ``(previous, new)`` sandbox states; when given, :func:`warn_sandbox_relaxation` runs first.
        sandbox_exceptions: ``(previous, new)`` gate inputs passed to :func:`warn_sandbox_relaxation`.
        sandbox_profile_would_emit: Passed to :func:`warn_sandbox_relaxation` as ``profile_would_emit``.

    Raises:
        WriteRootPolicyError: A root is hard-banned, or an external root is unaccepted.
    """
    if sandbox_states is not None:
        warn_sandbox_relaxation(sandbox_states[0], sandbox_states[1], framework_id=framework_id,
                                exceptions=sandbox_exceptions, profile_would_emit=sandbox_profile_would_emit)
    warn_prompt_root_relaxation(manifest, framework_id=framework_id, confined=confined)
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
