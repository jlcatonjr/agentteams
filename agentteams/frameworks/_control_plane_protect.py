"""_control_plane_protect.py — session write protection for the ``.agentteams`` control plane (METAPLAN R1, P00 D11).

Today's ``write_policy: orchestrator-only`` rules (P4a) deny the ledger to the session. WASM puts its runtime, verified
copies, scratch and the ``agentteams_wasm`` MCP bundle under ``.agentteams/`` as well, so the same write protection
must hold whenever WASM is on: ``protect_control_plane = protect_ledger or state.wasm``.

What each flag emits (the order under ``protect_ledger`` alone is unchanged, so those renders stay byte-identical):

* both: ``.agentteams`` denyWrite, ``Edit(/.agentteams/**)`` and ``Edit(/.claude/agents/**)`` (Claude); ``.agentteams``
  and ``.goose/recipes`` in the Goose Seatbelt deny; ``--protect .agentteams`` in the Linux Goose runner.
* ``protect_ledger`` only (runner-protocol rules): ``.agentteams-status`` denyWrite, the ``Read`` denies on the ledger
  and queue, ``Edit(/.agentteams-status/**)``. Rule-12 carve-out: with WASM on and no runner a session may read
  ``.agentteams/``; it holds no dispatch nonces without the runner, and the bundle and pins are public bytes.
* the control plane only: each MCP config path that launches ``agentteams_wasm`` (P11 lists them in
  ``manifest["wasm_wiring"]["protected_config_paths"]``), as a denyWrite plus an ``Edit`` rule.

Grok gets no rule: its profile cannot deny a write without denying the read (``_grok_sandbox_emit``), so P13 records
Grok Track-A as advisory. Integrity-pinned.
"""

from __future__ import annotations

from typing import Any

#: == ``_sandbox_emit.LEDGER_DIR_REL`` / ``STATUS_DIR_REL`` (test-locked; this module cannot import that one, which
#: imports this one).
LEDGER_DIR_REL = ".agentteams"
STATUS_DIR_REL = ".agentteams-status"


def protect_control_plane(manifest: dict[str, Any]) -> bool:
    """Return ``protect_ledger or state.wasm`` for ``manifest`` (computed once per emitter).

    Args:
        manifest: The team manifest.

    Returns:
        Whether to emit the control-plane write protection.

    Raises:
        ValueError: An invalid ``--wasm-sandbox`` value or a malformed ``wasm_sandbox`` record.
    """
    from agentteams.wasm_config import wasm_switch

    # The WASM switch alone (P01): the same value as ``effective_sandbox_state(manifest).wasm``, without building
    # grants, so R1 holds on every render path, including those with no CLI-set project root.
    return manifest.get("write_policy") == "orchestrator-only" or wasm_switch(manifest)[0]


def wasm_protected_config_paths(manifest: dict[str, Any]) -> tuple[str, ...]:
    """Return the project-relative MCP config paths that launch ``agentteams_wasm`` (R1; empty until P11).

    Args:
        manifest: The team manifest (``wasm_wiring.protected_config_paths``).

    Returns:
        The paths, de-duplicated in order.

    Raises:
        ValueError: Not a list, or a path that is absolute, contains ``..`` or a character outside
            ``[A-Za-z0-9._-]`` (``sandbox_effects.valid_rel``).
    """
    from agentteams.cli.sandbox_effects import valid_rel

    wiring = manifest.get("wasm_wiring")
    paths = wiring.get("protected_config_paths") if isinstance(wiring, dict) else None
    if not paths:
        return ()
    if not isinstance(paths, (list, tuple)):
        raise ValueError("wasm_wiring.protected_config_paths must be a list of project-relative paths")
    return tuple(dict.fromkeys(valid_rel(str(p)) for p in paths))


def control_plane_for(manifest: dict[str, Any]) -> tuple[str, ...] | None:
    """Return the emitter argument: None when the control plane is not protected, else its extra config paths.

    Args:
        manifest: The team manifest.

    Returns:
        ``None`` (off) or the (possibly empty) tuple of MCP config paths to protect beside ``.agentteams``.

    Raises:
        ValueError: As :func:`protect_control_plane` / :func:`wasm_protected_config_paths`.
    """
    return wasm_protected_config_paths(manifest) if protect_control_plane(manifest) else None


def write_denies(protect_ledger: bool, control_plane: tuple[str, ...] | None) -> tuple[str, ...]:
    """Return the Claude ``denyWrite`` entries for the two flags.

    Args:
        protect_ledger: ``write_policy: orchestrator-only``.
        control_plane: :func:`control_plane_for`'s value.

    Returns:
        The entries, in emission order.

    Raises:
        Nothing.
    """
    out: tuple[str, ...] = ()
    if protect_ledger or control_plane is not None:
        out = (LEDGER_DIR_REL, STATUS_DIR_REL) if protect_ledger else (LEDGER_DIR_REL,)
    return out + tuple(control_plane or ())


def permission_rules(protect_ledger: bool, control_plane: tuple[str, ...] | None) -> list[str]:
    """Return the Claude ``permissions.deny`` rules for the two flags.

    The sandbox's denyWrite doesn't bind the built-in Write/Edit tools, so they need ``Edit`` rules too; CLI and
    runner writes don't go through those tools. R1 (@security C4): the request queue and the runner's control plane
    hold raw dispatch nonces, so under the write policy no agent may read them (Claude Code 2.1.246 applies these Read
    denies to sandboxed commands too); the runner publishes to the session-readable ``.agentteams-status/`` tree,
    which nothing in the session may write. R7 (@security C11): the agent files carry each granted agent's
    ``agentteams_runner`` block (and, under WASM, each eligible agent's ``agentteams_wasm`` grant flags).

    Args:
        protect_ledger: ``write_policy: orchestrator-only``.
        control_plane: :func:`control_plane_for`'s value.

    Returns:
        The rules, in emission order (unchanged order under ``protect_ledger`` alone).

    Raises:
        Nothing.
    """
    rules: list[str] = []
    if protect_ledger or control_plane is not None:
        rules.append(f"Edit(/{LEDGER_DIR_REL}/**)")
    if protect_ledger:
        rules += [f"Read(/{LEDGER_DIR_REL}/**)", "Read(/.agentteams-queue/**)", f"Edit(/{STATUS_DIR_REL}/**)"]
    if protect_ledger or control_plane is not None:
        rules.append("Edit(/.claude/agents/**)")
    return rules + [f"Edit(/{p})" for p in control_plane or ()]


def seatbelt_denies(protect_ledger: bool, control_plane: tuple[str, ...] | None) -> tuple[str, ...]:
    """Return the project-relative paths the Goose Seatbelt control-plane deny adds for the two flags.

    The recipes carry each granted agent's MCP launch arguments (``agentteams_runner``; under WASM,
    ``agentteams_wasm``'s grant flags), so they are protected with the control plane.

    Args:
        protect_ledger: ``write_policy: orchestrator-only``.
        control_plane: :func:`control_plane_for`'s value.

    Returns:
        The paths, in emission order (unchanged order under ``protect_ledger`` alone).

    Raises:
        Nothing.
    """
    if not (protect_ledger or control_plane is not None):
        return ()
    base = (LEDGER_DIR_REL, STATUS_DIR_REL, ".goose/recipes") if protect_ledger else (LEDGER_DIR_REL, ".goose/recipes")
    return base + tuple(p for p in control_plane or () if p not in base)


def runner_protect_block(control_plane: tuple[str, ...] | None, root_var: str) -> tuple[str, str]:
    """Return ``(bash lines, launcher flag)`` that ``--protect`` the control plane in an emitted runner script.

    Only present paths are protected (the launcher refuses a missing ``--protect`` path). Each path is a validated
    plain relative path, so it is safe unquoted in the loop.

    Args:
        control_plane: :func:`control_plane_for`'s value; None emits nothing.
        root_var: The script's project-root variable name (e.g. ``REPO_ROOT``).

    Returns:
        ``("", "")`` when off.

    Raises:
        Nothing.
    """
    if control_plane is None:
        return "", ""
    paths = " ".join((LEDGER_DIR_REL, *control_plane))
    block = ('PROTECT=()\n'
             f'for p in {paths}; do [ -e "${root_var}/$p" ] && PROTECT+=( --protect "${root_var}/$p" ); done\n')
    return block, ' ${PROTECT[@]+"${PROTECT[@]}"}'


__all__ = [
    "control_plane_for",
    "permission_rules",
    "protect_control_plane",
    "runner_protect_block",
    "seatbelt_denies",
    "wasm_protected_config_paths",
    "write_denies",
]
