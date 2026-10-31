"""Sync projection keeps the previous WASM half (merge fix, P20 report): only the OS half is projected.

The projection does carry the model-substrate switch, so its on -> off is still checked on sync (P20).
Without it, a WASM-on team whose OS half engages the gate during sync was compared against an empty WASM half and
had a spurious ``sandbox-wasm-off`` planned, which a governed workspace refuses.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from agentteams.cli import os_sandbox_switch as S
from agentteams.cli import sandbox_relaxation_gate as gate
from agentteams.sandbox_state import SandboxState


def _state(*, os_on: bool, wasm_on: bool) -> SandboxState:
    return SandboxState(os=os_on, os_reason="brief" if os_on else "brief-off", wasm=wasm_on,
                        wasm_reason="brief" if wasm_on else "off", wasm_languages=("python",) if wasm_on else (),
                        wasm_grants={}, model_substrate="on" if wasm_on else None)


def _run(monkeypatch, tmp_path: Path, previous: SandboxState, projected: SandboxState, *, protect: bool = True) -> list:
    from agentteams.frameworks._control_plane_protect import SYNC_PROTECT_KEY

    seen: list = []
    manifest = {"os_sandbox": "on", "privilege_profile": "confined"}
    if protect:
        manifest[SYNC_PROTECT_KEY] = True
    monkeypatch.setattr("agentteams.sandbox_state.previous_sandbox_state", lambda *a, **k: previous)
    monkeypatch.setattr(gate, "authorize", lambda team, relaxations, consume: seen.extend(relaxations) or [])
    S.check_projected_relaxation(tmp_path, tmp_path, "claude", manifest, projected)
    return seen


def _actions(relaxations) -> list[str]:
    return [str(getattr(r, "action", None) or (r.get("action") if isinstance(r, dict) else r)) for r in relaxations]


def test_sync_of_a_wasm_team_plans_no_wasm_off(monkeypatch, tmp_path):
    previous = _state(os_on=True, wasm_on=True)
    projected = dataclasses.replace(previous, wasm=False, wasm_reason="off", wasm_languages=())
    assert not [a for a in _actions(_run(monkeypatch, tmp_path, previous, projected)) if "wasm-off" in a]


def test_sync_still_plans_the_os_off(monkeypatch, tmp_path):
    previous = _state(os_on=True, wasm_on=True)
    projected = dataclasses.replace(previous, os=False, os_reason="brief-off", wasm=False, wasm_reason="off",
                                    wasm_languages=())
    acts = _actions(_run(monkeypatch, tmp_path, previous, projected))
    assert any("sandbox-os-off" in a for a in acts) and not any("wasm-off" in a for a in acts)


def test_sync_without_r1_protection_still_plans_the_wasm_off(monkeypatch, tmp_path):
    """An older pin with no control_plane_protect re-emits the boundary without R1: that loss stays gated."""
    previous = _state(os_on=True, wasm_on=True)
    projected = dataclasses.replace(previous, wasm=False, wasm_reason="off", wasm_languages=())
    assert any("wasm-off" in a for a in _actions(_run(monkeypatch, tmp_path, previous, projected, protect=False)))


def test_capture_and_projection_carry_r1_protection(tmp_path):
    from agentteams import multi_sync as M
    from agentteams.frameworks._control_plane_protect import SYNC_PROTECT_KEY, protect_control_plane

    for brief in ({"privilege_profile": "confined", "wasm_sandbox": {"enabled": True}},
                  {"privilege_profile": "confined", "write_policy": "orchestrator-only"}):
        (tmp_path / "brief.json").write_text(json.dumps(brief), encoding="utf-8")
        priv = M._read_brief_privilege(tmp_path)
        assert priv.get("control_plane_protect") is True
        manifest = M._projected_privilege_manifest(tmp_path, "claude", priv)
        assert manifest.get(SYNC_PROTECT_KEY) is True and protect_control_plane(manifest)
    (tmp_path / "brief.json").write_text(json.dumps({"privilege_profile": "confined"}), encoding="utf-8")
    assert "control_plane_protect" not in M._read_brief_privilege(tmp_path)
    plain = M._projected_privilege_manifest(tmp_path, "claude", {"privilege_profile": "confined"})
    assert SYNC_PROTECT_KEY not in plain and not protect_control_plane(plain)
