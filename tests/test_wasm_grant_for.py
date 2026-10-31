"""P01 D8 / METAPLAN R9: ``wasm_grant_for(state, agent) -> Grant | None`` — None iff WASM is off."""

from __future__ import annotations

from agentteams.sandbox_state import SandboxState, wasm_grant_for
from agentteams.wasm_exec import COMPUTE_ONLY, DEFAULT_LIMITS, Grant, Mount


def _state(wasm: bool, grants=None) -> SandboxState:
    return SandboxState(os=True, os_reason="brief", wasm=wasm, wasm_reason="brief" if wasm else "default-off",
                        wasm_languages=("python",) if wasm else (), wasm_grants=grants or {})


def test_off_is_none_even_with_a_grant_in_the_state():
    grant = Grant(mounts=(Mount("/p/docs", "/project/docs", "ro"),), limits=DEFAULT_LIMITS)
    assert wasm_grant_for(_state(False), "analyst") is None
    assert wasm_grant_for(_state(False, {"analyst": grant}), "analyst") is None


def test_on_without_an_entry_is_compute_only_itself():
    assert wasm_grant_for(_state(True), "analyst") is COMPUTE_ONLY


def test_on_with_an_entry_is_that_grant():
    grant = Grant(mounts=(Mount("/p/.agentteams/wasm/scratch/analyst", "/scratch", "rw"),), limits=DEFAULT_LIMITS)
    state = _state(True, {"analyst": grant})
    assert wasm_grant_for(state, "analyst") is grant
    assert wasm_grant_for(state, "navigator") is COMPUTE_ONLY
