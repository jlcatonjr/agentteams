"""P20 D5: the projected ``.agentteams/sandbox-state.json`` is session-write-protected wherever a sandbox is emitted.

* Claude: a ``denyWrite`` entry and an ``Edit(/.agentteams/sandbox-state.json)`` rule, only when projecting, and not
  twice when the whole ``.agentteams`` is already protected (WASM on, METAPLAN R1 / P00 D11, or the write policy).
* Goose: the Seatbelt control-plane deny names the file (macOS) and the Linux runner ``--protect``s it.
* Codex / Grok runners already ``--protect`` the whole ``.agentteams`` (unchanged). Launcher bytes unchanged.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import sandbox_projection as sp
from agentteams.frameworks import _control_plane_protect as cpp
from agentteams.frameworks.registry import FRAMEWORKS

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"
FILE = sp.PROJECTION_REL


def _extras(framework: str, wasm: dict | None, **brief_extra) -> dict[str, str]:
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "confined", **brief_extra}
    if wasm is not None:
        brief["wasm_sandbox"] = wasm
    manifest = analyze.build_manifest(brief, framework=framework)
    return dict(FRAMEWORKS[framework]().extra_output_files(manifest))


def _claude(wasm: dict | None) -> tuple[list[str], list[str]]:
    data = json.loads(_extras("claude", wasm)["../settings.hooks.example.json"])
    return data["sandbox"]["filesystem"]["denyWrite"], data["permissions"]["deny"]


def test_the_rel_path_is_locked():
    assert cpp.SUBSTRATE_FILE_REL == sp.PROJECTION_REL == ".agentteams/sandbox-state.json"


@pytest.mark.parametrize("value", ["on", "off"])
def test_claude_denies_the_file_only_when_projecting(value):
    deny_write, deny = _claude({"model_substrate": value})
    assert FILE in deny_write and f"Edit(/{FILE})" in deny
    deny_write, deny = _claude(None)
    assert FILE not in deny_write and f"Edit(/{FILE})" not in deny


def test_claude_emits_no_duplicate_when_wasm_already_protects_the_control_plane():
    deny_write, deny = _claude({"enabled": True, "model_substrate": "on"})
    assert ".agentteams" in deny_write and "Edit(/.agentteams/**)" in deny
    assert FILE not in deny_write and f"Edit(/{FILE})" not in deny
    assert len(deny_write) == len(set(deny_write)) and len(deny) == len(set(deny))


def test_substrate_paths_follow_the_control_plane_flag():
    base = {"agent_slug_list": []}
    assert cpp.substrate_protect_paths(base) == ()
    assert cpp.substrate_protect_paths({**base, "_wasm_substrate_cli": "on"}) == (FILE,)
    assert cpp.substrate_protect_paths({**base, "_wasm_substrate_kept": "on"}) == (FILE,)
    assert cpp.substrate_protect_paths({**base, "_wasm_substrate_cli": "on", "write_policy": "orchestrator-only"}) == ()
    assert cpp.substrate_protect_paths({**base, "_wasm_substrate_cli": "on", "_wasm_sandbox_cli": "on"}) == ()


def test_the_helpers_are_unchanged_without_a_substrate():
    for ledger in (False, True):
        for plane in (None, (), ("x.json",)):
            assert cpp.write_denies(ledger, plane) == cpp.write_denies(ledger, plane, ())
            assert cpp.permission_rules(ledger, plane) == cpp.permission_rules(ledger, plane, ())
            assert cpp.seatbelt_denies(ledger, plane) == cpp.seatbelt_denies(ledger, plane, ())
            assert cpp.runner_protect_block(plane, "R") == cpp.runner_protect_block(plane, "R", ())
    assert cpp.write_denies(False, None, (FILE,)) == (FILE,)
    assert cpp.seatbelt_denies(False, None, (FILE,)) == (FILE,)
    block, flag = cpp.runner_protect_block(None, "REPO_ROOT", (FILE,))
    assert f"for p in {FILE}; do" in block and "--protect" in block and flag


def test_goose_seatbelt_denies_the_file(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    profile = next(v for k, v in _extras("goose", {"model_substrate": "on"}).items() if k.endswith("sandbox.sb"))
    deny_block = profile.split(";; --- Control-plane protection", 1)[1].split("\n)\n", 1)[0]
    assert "sandbox-state.json" in deny_block
    plain = next(v for k, v in _extras("goose", None).items() if k.endswith("sandbox.sb"))
    assert "sandbox-state.json" not in plain


def test_goose_linux_runner_protects_the_file(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    runner = next(v for k, v in _extras("goose", {"model_substrate": "on"}).items()
                  if k.endswith("confined-run.example.sh"))
    assert f"for p in {FILE}; do" in runner
    plain = next(v for k, v in _extras("goose", None).items() if k.endswith("confined-run.example.sh"))
    assert "sandbox-state.json" not in plain


@pytest.mark.parametrize("framework", ["codex", "grok"])
def test_codex_and_grok_runners_already_protect_the_whole_control_plane(framework):
    runners = [v for k, v in _extras(framework, {"model_substrate": "on"}).items() if k.endswith(".sh")]
    assert any('--protect "$ROOT/' in r and ".agentteams" in r for r in runners)


def test_launcher_bytes_unchanged_by_the_projection():
    from tests.test_mac_sandbox_augment import EXPECTED_LAUNCHER_SHA256

    for wasm in (None, {"model_substrate": "on"}):
        launcher = next(v for k, v in _extras("claude", wasm).items() if k.endswith("confine-run.sh"))
        assert hashlib.sha256(launcher.encode("utf-8")).hexdigest() == EXPECTED_LAUNCHER_SHA256
