"""wasm_sandbox_switch.py — the generate-time WASM switch: ``--wasm-sandbox``, validation, and the D6 notices (P01).

Kept out of ``cli/artifacts.py`` (over the size ceiling) and beside ``cli/os_sandbox_switch.py``. Two jobs, both
called from ``cli.artifacts.finalize_privilege_wiring``:

* :func:`apply_wasm_switch` carries ``--wasm-sandbox`` as the transient
  :data:`~agentteams.wasm_config.WASM_SANDBOX_CLI_KEY` and validates the WASM half of the effective state up front,
  so a bad value, a symlinked read path or an unknown project root refuses the render with a WASM message (fail
  closed) before anything is emitted.
* :func:`advise_wasm` prints the WASM ENABLED / inert-grants notices and the D6 composition advisories
  (``wasm-without-os-sandbox``, ``wasm-only-host``) and records the advisories in ``manifest["advisories"]``. It
  never claims enforcement: the per-provider status is P13's notice.

No new gate call site: P00's ``engage_relaxation_gate`` sees the WASM fields through
:func:`~agentteams.sandbox_state.effective_sandbox_state` (``sandbox-wasm-off`` on on->off, ``wasm-grant-widen``
with exactly the added tokens).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentteams.sandbox_state import SandboxState


class WasmSwitchError(ValueError):
    """An invalid ``--wasm-sandbox`` value or ``wasm_sandbox`` block, or a WASM grant that cannot be built."""


def apply_wasm_switch(manifest: dict, wasm_sandbox_cli: str | None, *, project_root: Path | None = None,
                      team_dir: Path | None = None) -> None:
    """Carry ``--wasm-sandbox`` onto ``manifest`` and validate the WASM half of the effective state.

    P20: also validates ``--wasm-substrate`` (the transient ``WASM_SUBSTRATE_CLI_KEY`` ``cli/generate.py`` set) and,
    given the project root, either preflights the projected ``.agentteams/sandbox-state.json`` (no symlinked dir or
    file; fail closed before anything is emitted) or, when neither the brief nor argv sets the switch, carries a
    file the previous render left as the *kept* floor (``WASM_SUBSTRATE_KEPT_KEY``), so the relaxation gate sees no
    change while the floor stays.

    ``--framework wasm`` (P08 §4.6) is itself the opt-in: when neither the brief nor ``--wasm-sandbox`` names the
    switch, the transient :data:`~agentteams.wasm_config.WASM_FRAMEWORK_KEY` turns WASM on (reason ``framework``).
    An explicit off (brief ``enabled: false`` or ``--wasm-sandbox off``) refuses: the runtime has no non-WASM mode.

    Args:
        manifest: The team manifest (mutated: the transient CLI / framework key; the kept key). The
            CLI-set project root must already be on it (``write_root_policy.begin``) for grants with mounts.
        wasm_sandbox_cli: ``--wasm-sandbox`` (operator argv), or None.
        project_root: The project root (P20 projection checks), or None to skip them.
        team_dir: This render's agents dir, carried as the transient ``sandbox_projection.TEAM_DIR_KEY`` for the
            projection's ``team`` field (the project root when None).

    Raises:
        WasmSwitchError: A bad value, a malformed block, a refused read path, a mount with no project root, a
            projection that cannot be written safely, or WASM explicitly off for ``--framework wasm``.
        ImportError: WASM is on and ``agentteams.wasm_exec`` is not importable (fail closed).
    """
    from agentteams.wasm_config import (
        WASM_FRAMEWORK_KEY,
        WASM_SANDBOX_CLI_KEY,
        WASM_SUBSTRATE_KEPT_KEY,
        substrate_switch,
        validate_wasm_sandbox_cli,
        wasm_state_fields,
        wasm_switch,
    )

    try:
        if wasm_sandbox_cli is not None:
            manifest[WASM_SANDBOX_CLI_KEY] = validate_wasm_sandbox_cli(wasm_sandbox_cli)
        if manifest.get("framework") == "wasm":
            on, reason = wasm_switch(manifest)
            if not on and reason != "default-off":
                raise WasmSwitchError(f"--framework wasm needs WASM confinement, but it is switched off ({reason}): "
                                      "the WASM agent runtime has no non-WASM mode. Remove the off switch.")
            if not on:
                manifest[WASM_FRAMEWORK_KEY] = True
                print("  🧩 --framework wasm turns WASM confinement on for this team (reason: framework).")
        wasm_state_fields(manifest)
        manifest.pop(WASM_SUBSTRATE_KEPT_KEY, None)  # only ever derived from disk, here
        _value, source = substrate_switch(manifest)
        if project_root is not None:
            from agentteams import sandbox_projection

            manifest[sandbox_projection.TEAM_DIR_KEY] = Path(team_dir if team_dir is not None else project_root)
            if source is None:
                kept = sandbox_projection.kept_value(Path(project_root))
                if kept is not None:
                    manifest[WASM_SUBSTRATE_KEPT_KEY] = kept
            else:
                sandbox_projection.preflight(Path(project_root))
    except ValueError as exc:
        raise WasmSwitchError(str(exc)) from exc


def advise_wasm(manifest: dict, state: SandboxState, framework_id: str, *, mechanism: bool | None = None) -> None:
    """Print the WASM switch notices and record the D6 composition advisories.

    Args:
        manifest: The team manifest (mutated: ``advisories``).
        state: The effective sandbox state.
        framework_id: The target framework.
        mechanism: The live OS-mechanism probe result, or None to probe it (only when WASM is on).

    Raises:
        ValueError: A malformed ``wasm_sandbox`` record (already validated by :func:`apply_wasm_switch`).
    """
    from agentteams.wasm_config import composition_advisories, manifest_wasm_config, wasm_switch_set

    if not wasm_switch_set(manifest):
        return
    cfg = manifest_wasm_config(manifest)
    if state.wasm:
        widened = sorted(a for a, g in state.wasm_grants.items() if g.mounts)
        print(f"  🧩 WASM confinement is ON for this team ({state.wasm_reason}): languages "
              f"{', '.join(state.wasm_languages)}; compute-only grant (stdio, no files, no env, no network) "
              f"{'except for ' + ', '.join(widened) if widened else 'for every agent'}. Opt-in; turning it off "
              "again is constraint-relaxing (sandbox-wasm-off).")
    else:
        print(f"  ·  WASM confinement is OFF for this team ({state.wasm_reason}).")
        if cfg.agents:
            print(f"  ·  wasm_sandbox grants ({', '.join(cfg.agents)}) are inert while wasm_sandbox is off; turning "
                  "WASM on makes them a widening the relaxation gate checks (wasm-grant-widen).")
    for advisory in composition_advisories(state, framework_id, mechanism=mechanism):
        print(f"  NOTE (WASM, D6): {advisory['message']}", file=sys.stderr)
        manifest.setdefault("advisories", []).append(advisory)


def report_wasm_wiring(manifest: dict, state: SandboxState, framework_id: str, *,
                       team_dir: Path | None = None) -> None:
    """Record ``manifest["wasm_wiring"]`` for the target framework and print its ``[WASM-WIRING]`` line (P11).

    Args:
        manifest: The team manifest (mutated: ``wasm_wiring``, ``advisories``). Untouched when WASM is off.
        state: The effective sandbox state.
        framework_id: The target framework.
        team_dir: This render's agents dir; with WASM on it must be the framework's canonical one, or the files that
            launch ``agentteams_wasm`` would land where no control-plane deny names them (None skips the check).

    Raises:
        ValueError: A widened WASM grant on a framework that takes one shared compute-only copy (I3), or WASM on with
            a non-canonical agents dir.
    """
    from agentteams.frameworks._wasm_wiring import agents_dir_problem, print_report

    problem = agents_dir_problem(manifest, framework_id, team_dir)
    if problem:
        raise ValueError(problem)
    print_report(manifest, framework_id, state)


def install_wasm_binary(manifest: dict, project_root: Path) -> None:
    """Write the ``agentteams_wasm`` bundle's binary file beside the emitted text bundle (P11).

    The install root is derived, never searched for (``_wasm_wiring.install_root``: the CLI's project root, or the
    grandparent of the framework's canonical agents dir when ``--output`` named it). A write failure (``OSError``)
    is printed, not raised (``--check-wiring`` then reports the missing file); anything else stops the render.

    Args:
        manifest: The team manifest.
        project_root: The CLI's project root (the pre-normalisation ``--output`` path).

    Raises:
        SystemExit: Exit code 1, after an ``Error:`` line, when the server is not installed at the derived root,
            a bundle file does not match its pin, or ``.agentteams`` / ``bin`` is a symlink (fail closed: never
            downgraded to a warning).
    """
    from agentteams.frameworks._wasm_wiring import install_binary, install_root

    root = install_root(str(manifest.get("framework") or ""), Path(project_root).resolve())
    try:
        for rel in install_binary(manifest, root):
            print(f"  ✓  Installed agentteams_wasm bundle file: {rel}")
    except OSError as exc:
        print(f"  !  agentteams_wasm bundle install failed ({exc}); the server refuses to start until it is "
              "re-rendered", file=sys.stderr)
    except ValueError as exc:   # fail closed, with a message instead of a traceback
        print(f"Error: agentteams_wasm bundle install refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


__all__ = ["WasmSwitchError", "advise_wasm", "apply_wasm_switch", "install_wasm_binary", "report_wasm_wiring"]
