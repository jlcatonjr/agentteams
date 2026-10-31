"""os_sandbox_switch.py — the generate-time OS-sandbox switch and its relaxation-gate engagement (P00 D7).

Carved out of ``cli/artifacts.py`` (wave-2 @code-hygiene H1). Two jobs:

* :func:`resolve_os_switch` sets ``manifest["host_features"]`` from the dedicated switch (``--os-sandbox`` beats the
  brief's ``os_sandbox``; an explicit argv ``*:sandbox`` token beats a brief ``off``; ``--os-sandbox off`` with an
  explicit token is refused) and prints the ENABLED/OFF notice.
* :func:`engage_relaxation_gate` runs the P15 sandbox-relaxation gate (Rule 15) and records ``sandbox_state``.

**When the gate engages** (:func:`gate_engaged`): a switch is in play (brief ``os_sandbox`` / ``--os-sandbox``, P01:
brief ``wasm_sandbox`` / ``--wasm-sandbox``), WASM is on, the previous render recorded a ``sandbox_state``, WASM
evidence on disk (an installed runtime or ``agentteams_wasm`` server) meets a WASM-off render (P01: so a
``sandbox-wasm-off:<fw>`` cannot be stepped around by deleting the build-log), or (wave-2 @security S1) the previous state
(:func:`~agentteams.sandbox_state.previous_sandbox_state`: the record OR on-disk boundary evidence) had the OS
sandbox on and the new state has it off, whatever ``os_reason`` says. So ``confined`` -> ``cooperative`` with a
boundary on disk is a ``sandbox-os-off:<fw>`` relaxation: refused in a governed workspace without the operator's
Ed25519-signed decision, a ``SANDBOX RELAXATION`` notice otherwise. A team with no record and no evidence is not
engaged and keeps a byte-identical build-log (the P00 carve-out now covers only that case).

**Gate inputs** (:func:`gate_exceptions`, wave-3 @security S2/S5): the ``native_exec_keep`` set (D12: the default
``navigator`` / ``git-operations`` unless the brief lists its own) reaches the gate whenever WASM is on, whatever
``write_policy`` says; the runner program modes come from ``confined_programs`` under ``orchestrator-only``. Leaving
``orchestrator-only`` after runner programs were recorded counts every recorded ``sandbox: wasm`` agent as switched
(the runner no longer serves it), so ``wasm-to-os-switch`` is still gated.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentteams.sandbox_state import SandboxExceptions, SandboxState

_CONFINING = frozenset({"confined", "exclusive"})


class OsSwitchError(ValueError):
    """An invalid ``os_sandbox`` / ``--os-sandbox`` value, or ``--os-sandbox off`` with an explicit token."""


def resolve_os_switch(manifest: dict, explicit_tokens: list[str], framework_id: str) -> SandboxState:
    """Set ``manifest["host_features"]`` by the dedicated OS switch and print the switch notice.

    Args:
        manifest: The team manifest (mutated: ``host_features`` and the transient explicit-token key).
        explicit_tokens: Tokens parsed from ``--target-host-features``.
        framework_id: The target framework id.

    Returns:
        The effective :class:`~agentteams.sandbox_state.SandboxState`.

    Raises:
        OsSwitchError: An invalid ``os_sandbox`` / ``--os-sandbox`` value, or ``--os-sandbox off`` together with an
            explicit ``*:sandbox`` token (the caller maps it to ``PrivilegeConfinementError``).
    """
    from agentteams.host_features import merge_profile_features
    from agentteams.sandbox_state import (
        EXPLICIT_SANDBOX_TOKENS_KEY,
        OS_SANDBOX_CLI_KEY,
        effective_sandbox_state,
        os_switch_set,
        validate_os_sandbox,
    )

    # --os-sandbox (transient, operator argv) beats the brief; an explicit argv *:sandbox token beats a brief "off"
    # (both are the operator's; the brief is agent-writable), and --os-sandbox off together with an explicit token
    # is refused (never pick one silently).
    explicit_sandbox = [str(t) for t in explicit_tokens if str(t).endswith(":sandbox")]
    if explicit_sandbox:
        manifest[EXPLICIT_SANDBOX_TOKENS_KEY] = explicit_sandbox  # transient; never persisted
    try:
        cli_value = validate_os_sandbox(manifest.get(OS_SANDBOX_CLI_KEY))
        brief_value = validate_os_sandbox(manifest.get("os_sandbox"))
        effective_sandbox_state(manifest)  # raises on the --os-sandbox off + explicit token conflict
    except ValueError as exc:
        raise OsSwitchError(str(exc)) from exc
    if cli_value is None and brief_value == "off" and explicit_sandbox:
        merged = merge_profile_features(explicit_tokens, manifest.get("privilege_profile"), framework_id)
        manifest["host_features"] = [t for t in merged if not str(t).endswith(":sandbox") or t in explicit_sandbox]
    else:
        manifest["host_features"] = merge_profile_features(
            explicit_tokens, manifest.get("privilege_profile"), framework_id,
            os_sandbox=cli_value or brief_value,
        )
    state = effective_sandbox_state(manifest)
    if manifest["host_features"]:
        print(f"  Host features: {', '.join(manifest['host_features'])}")
    # Default-on notice (2026-W39): privilege_profile now defaults to "confined", so a team
    # that never set the field gets write-confinement emitted. Mirrors the
    # enforce_decision_signing "default on at update, notify after, opportunity to switch
    # off" contract — name the opt-out so the tightening is never silent. Inert until the
    # operator merges the emitted sandbox example (never writes live settings/config).
    # P00: printed from the effective state, naming the dedicated os_sandbox opt-out.
    if state.os and (manifest.get("privilege_profile") in _CONFINING or os_switch_set(manifest)):
        print(
            "  🛡  Workspace sandbox (OS boundary) is ENABLED for this team "
            f"({state.os_reason}; privilege_profile defaults to confined as of 2026-W39). An OS "
            "write-confinement boundary is emitted for qualifying providers (inert until you merge "
            "the emitted settings/config example). To opt out, set \"os_sandbox\": \"off\" in the "
            "brief (or pass --os-sandbox off) and re-run --update."
        )
    elif not state.os and os_switch_set(manifest):
        print(
            f"  ·  Workspace sandbox (OS boundary) is OFF for this team ({state.os_reason}): no OS "
            "boundary is emitted for this framework; the rest of privilege_profile "
            f"({manifest.get('privilege_profile')}) still applies."
        )
    return state


def gate_engaged(manifest: dict, state: SandboxState, previous: SandboxState | None, *,
                 recorded: bool, wasm: bool = True) -> bool:
    """Return whether the P15 relaxation gate must run for this render (wave-2 S1; P01 adds the WASM switch).

    Args:
        manifest: The team manifest (the switch keys).
        state: The new effective state.
        previous: :func:`~agentteams.sandbox_state.previous_sandbox_state` (record OR evidence), or None.
        recorded: Whether the previous render recorded a ``sandbox_state``.
        wasm: Whether this render derives the WASM half (False for the multi-framework sync projection, which
            projects no WASM half: a WASM on -> off there would be an artefact of the projection, not a change).

    Returns:
        True when an OS or WASM switch is in play, WASM is on, a state was recorded, the OS sandbox (or, with
        ``wasm``, WASM: a runtime or the ``agentteams_wasm`` server on disk) goes from on to off, or (P20) the
        model-substrate switch is set or goes from on to off/unset.

    Raises:
        Nothing.
    """
    from agentteams.sandbox_state import os_switch_set
    from agentteams.wasm_config import wasm_switch_set

    os_off = previous is not None and previous.os and not state.os
    wasm_off = wasm and previous is not None and previous.wasm and not state.wasm
    # P20: a set model-substrate switch engages the full render (so its state is recorded); an on -> off/unset change
    # engages both paths, even with no record (the projected file alone is the evidence).
    substrate = (wasm and state.model_substrate is not None) or (
        previous is not None and previous.model_substrate == "on" and state.model_substrate != "on")
    switch = os_switch_set(manifest) or (wasm and wasm_switch_set(manifest))
    return switch or state.wasm or recorded or os_off or wasm_off or substrate


def engage_relaxation_gate(
    manifest: dict, state: SandboxState, framework_id: str, project_root: Path, *, team_dir: Path | None,
    dry_run: bool, brief_confined_programs: Any = None,
) -> tuple[SandboxState | None, SandboxState, tuple[SandboxExceptions | None, SandboxExceptions] | None] | None:
    """Run the P15 sandbox-relaxation gate for this render and record the state (P00 D7, wave-2 S1).

    Engaged per :func:`gate_engaged`. A team with no record and no on-disk boundary evidence that never used the
    switch is not engaged and keeps a byte-identical build-log.

    Args:
        manifest: The team manifest (mutated: ``sandbox_relaxations``, ``sandbox_state``).
        state: The effective state.
        framework_id: The target framework.
        project_root: The project root (evidence files, record paths).
        team_dir: The team dir (build-log, decisions log, exception registry); the project root when None.
        dry_run: Check only: consume nothing, register and retire nothing.
        brief_confined_programs: The brief's ``confined_programs``. With the ``native_exec_keep`` set they become
            the gate's ``SandboxExceptions`` (:func:`gate_exceptions`: ``native-exec-keep``, ``wasm-to-os-switch``)
            and are recorded as ``sandbox_exceptions``; the gate is then engaged too, so neither can step around it.

    Returns:
        ``(previous, new, exceptions)`` for the ``SANDBOX RELAXATION`` notice (``exceptions`` is
        :func:`gate_exceptions`, so native-exec keeps and runner switches are noticed too), or None when not
        engaged.

    Raises:
        SandboxRelaxationError: A relaxation is refused in a governed workspace (nothing consumed).
        ImportError: The record carries WASM grants and ``agentteams.wasm_exec`` is not importable (fail closed).
        WriteRootPolicyError: The operator ``confined_programs`` file fails its custody checks.
    """
    from agentteams.cli import sandbox_relaxation_gate as gate
    from agentteams.sandbox_state import has_recorded_state, previous_sandbox_exceptions, previous_sandbox_state

    team = Path(team_dir) if team_dir is not None else Path(project_root)
    exceptions = gate_exceptions(manifest, state, project_root, previous_sandbox_exceptions(team),
                                 brief_confined_programs)
    old = previous_sandbox_state(team, project_root, framework_id)
    if not (gate_engaged(manifest, state, old, recorded=has_recorded_state(team)) or exceptions):
        return None
    relaxations = gate.plan_relaxations(
        old, state, framework_id=framework_id, profile_would_emit=manifest.get("privilege_profile") in _CONFINING,
        exceptions=exceptions)
    manifest["sandbox_relaxations"] = gate.authorize(team, relaxations, consume=not dry_run)
    gate.retire_on_tighten(team, old, state, framework_id=framework_id, exceptions=exceptions, dry_run=dry_run)
    manifest["sandbox_state"] = state.to_record(project_root=Path(project_root))
    if exceptions is not None:
        manifest["sandbox_exceptions"] = exceptions[1].to_record()
    return old, state, exceptions


def gate_exceptions(manifest: dict, state: SandboxState, project_root: Path, previous_exc: SandboxExceptions | None,
                    brief_confined_programs: Any = None
                    ) -> tuple[SandboxExceptions | None, SandboxExceptions] | None:
    """Return the gate's ``(previous, new)`` :class:`~agentteams.sandbox_state.SandboxExceptions`, or None.

    * ``native_exec_keep`` (S2): the effective keep set (``wasm_config.manifest_wasm_config``; D12 default
      ``navigator`` / ``git-operations``) whenever WASM is on, under any ``write_policy``.
    * Runner programs: under ``write_policy: orchestrator-only``, the modes of the ``confined_programs`` the runner
      would load (operator file, else the brief). Otherwise the runner serves nothing (S5): every previously recorded
      ``sandbox: wasm`` or ``os`` agent is carried as ``os_programs``, so a recorded wasm agent is a fresh
      ``wasm-to-os-switch`` and a switched one keeps being re-checked against its standing exception.

    Args:
        manifest: The team manifest (``write_policy``, ``wasm_sandbox``, ``agent_slug_list``).
        state: The new effective state.
        project_root: The project root (the operator ``confined_programs`` file).
        previous_exc: :func:`~agentteams.sandbox_state.previous_sandbox_exceptions` of the team.
        brief_confined_programs: The brief's ``confined_programs``.

    Returns:
        None when nothing is or was recorded (a team that never kept native exec or used the runner's WASM keeps a
        byte-identical build-log), else ``(previous_exc, new)``.

    Raises:
        WriteRootPolicyError: The operator ``confined_programs`` file fails its custody checks.
        ValueError: A malformed ``wasm_sandbox`` block.
    """
    from agentteams.sandbox_state import SandboxExceptions
    from agentteams.wasm_config import manifest_wasm_config

    keep = frozenset(manifest_wasm_config(manifest).native_exec_keep) if state.wasm else frozenset()
    if manifest.get("write_policy") == "orchestrator-only":
        from agentteams import runner_wasm
        from agentteams._sandbox_profiles import ConfinementError
        from agentteams.cli.write_root_policy import WriteRootPolicyError

        try:
            new_exc = runner_wasm.runner_exceptions(runner_wasm.effective_raw(Path(project_root),
                                                                              brief_confined_programs),
                                                    native_exec_keep=keep)
        except ConfinementError as exc:   # an unreadable operator file: the runner refuses it too
            raise WriteRootPolicyError(f"the operator confined_programs file: {exc}") from exc
    else:
        carried = previous_exc.wasm_programs | previous_exc.os_programs if previous_exc is not None else frozenset()
        new_exc = SandboxExceptions(native_exec_keep=keep, os_programs=carried)
    # Engaged once a keep or a runner wasm program is (or was recorded); once recorded, every later render re-checks
    # the standing exceptions.
    if new_exc.wasm_programs or new_exc.native_exec_keep or previous_exc is not None:
        return previous_exc, new_exc
    return None


def check_projected_relaxation(agents_dir: Path, root: Path, framework_id: str, manifest: dict | None,
                               state: SandboxState) -> tuple[SandboxState | None, SandboxState] | None:
    """Check (never consume) the gate for a multi-framework sync projection (P00 D7, wave-2 S1).

    Engaged as :func:`gate_engaged` with ``recorded=False``: a switch is in play or the OS sandbox goes from on to
    off. The sync path records no state and projects no WASM half, so a recorded state alone does not engage it
    (that would plan a ``sandbox-wasm-off`` for every WASM team the projection merely re-syncs).

    Args:
        agents_dir: The framework's agents dir (the team dir: build-log, decisions log, registry).
        root: The project root (evidence files).
        framework_id: The framework being projected.
        manifest: The projected privilege manifest, or None (no privilege block).
        state: The projected effective state.

    Returns:
        ``(previous, new)`` for the notice when engaged, else None.

    Raises:
        SandboxRelaxationError: The relaxation is refused (governed workspace, no signed decision or active
            exception).
    """
    from agentteams.cli import sandbox_relaxation_gate as gate
    from agentteams.sandbox_state import previous_sandbox_state

    old = previous_sandbox_state(agents_dir, root, framework_id)
    if not gate_engaged(manifest or {}, state, old, recorded=False, wasm=False):
        return None
    from agentteams.frameworks._control_plane_protect import SYNC_PROTECT_KEY

    if old is not None and (manifest or {}).get(SYNC_PROTECT_KEY) is True:
        # The projection derives the OS half and the model-substrate switch, not the agent-WASM half. When it carries
        # the team's R1 control-plane protection (SYNC_PROTECT_KEY), the WASM boundary it re-emits is unchanged, so
        # that half is the previous one; comparing against an empty one would plan a spurious sandbox-wasm-off.
        # Without the flag (an older pin), the re-emitted files drop R1, and the sandbox-wasm-off stays planned.
        state = dataclasses.replace(state, wasm=old.wasm, wasm_reason=old.wasm_reason,
                                    wasm_languages=old.wasm_languages, wasm_grants=old.wasm_grants)
    relaxations = gate.plan_relaxations(
        old, state, framework_id=framework_id,
        profile_would_emit=(manifest or {}).get("privilege_profile") in _CONFINING)
    gate.authorize(agents_dir, relaxations, consume=False)
    return old, state


__all__ = ["OsSwitchError", "check_projected_relaxation", "engage_relaxation_gate", "gate_engaged", "gate_exceptions",
           "resolve_os_switch"]
