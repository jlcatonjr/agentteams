"""Sync projection keeps the previous WASM half (merge fix, P20 report): only the OS half is projected.

Without it, a WASM-on team whose OS half engages the gate during sync was compared against an empty WASM half and
had a spurious ``sandbox-wasm-off`` planned, which a governed workspace refuses.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from agentteams.cli import os_sandbox_switch as S
from agentteams.cli import sandbox_relaxation_gate as gate
from agentteams.sandbox_state import SandboxState


def _state(*, os_on: bool, wasm_on: bool) -> SandboxState:
    return SandboxState(os=os_on, os_reason="brief" if os_on else "brief-off", wasm=wasm_on,
                        wasm_reason="brief" if wasm_on else "off", wasm_languages=("python",) if wasm_on else (),
                        wasm_grants={}, model_substrate="on" if wasm_on else None)


def _run(monkeypatch, tmp_path: Path, previous: SandboxState, projected: SandboxState) -> list:
    seen: list = []
    monkeypatch.setattr("agentteams.sandbox_state.previous_sandbox_state", lambda *a, **k: previous)
    monkeypatch.setattr(gate, "authorize", lambda team, relaxations, consume: seen.extend(relaxations) or [])
    S.check_projected_relaxation(tmp_path, tmp_path, "claude", {"os_sandbox": "on", "privilege_profile": "confined"},
                                 projected)
    return seen


def _actions(relaxations) -> list[str]:
    return [str(getattr(r, "action", None) or (r.get("action") if isinstance(r, dict) else r)) for r in relaxations]


def test_sync_of_a_wasm_team_plans_no_wasm_off(monkeypatch, tmp_path):
    previous = _state(os_on=True, wasm_on=True)
    projected = dataclasses.replace(previous, wasm=False, wasm_reason="off", wasm_languages=(), model_substrate=None)
    assert not [a for a in _actions(_run(monkeypatch, tmp_path, previous, projected)) if "wasm-off" in a]


def test_sync_still_plans_the_os_off(monkeypatch, tmp_path):
    previous = _state(os_on=True, wasm_on=True)
    projected = dataclasses.replace(previous, os=False, os_reason="brief-off", wasm=False, wasm_reason="off",
                                    wasm_languages=(), model_substrate=None)
    acts = _actions(_run(monkeypatch, tmp_path, previous, projected))
    assert any("sandbox-os-off" in a for a in acts) and not any("wasm-off" in a for a in acts)
