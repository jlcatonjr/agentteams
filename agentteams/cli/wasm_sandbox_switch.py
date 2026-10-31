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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentteams.sandbox_state import SandboxState


class WasmSwitchError(ValueError):
    """An invalid ``--wasm-sandbox`` value or ``wasm_sandbox`` block, or a WASM grant that cannot be built."""


def apply_wasm_switch(manifest: dict, wasm_sandbox_cli: str | None) -> None:
    """Carry ``--wasm-sandbox`` onto ``manifest`` and validate the WASM half of the effective state.

    Args:
        manifest: The team manifest (mutated: the transient CLI key when the flag is given). The CLI-set project
            root must already be on it (``write_root_policy.begin``) for grants with mounts.
        wasm_sandbox_cli: ``--wasm-sandbox`` (operator argv), or None.

    Raises:
        WasmSwitchError: A bad value, a malformed block, a refused read path, or a mount with no project root.
        ImportError: WASM is on and ``agentteams.wasm_exec`` is not importable (fail closed).
    """
    from agentteams.wasm_config import WASM_SANDBOX_CLI_KEY, validate_wasm_sandbox_cli, wasm_state_fields

    try:
        if wasm_sandbox_cli is not None:
            manifest[WASM_SANDBOX_CLI_KEY] = validate_wasm_sandbox_cli(wasm_sandbox_cli)
        wasm_state_fields(manifest)
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


__all__ = ["WasmSwitchError", "advise_wasm", "apply_wasm_switch"]
