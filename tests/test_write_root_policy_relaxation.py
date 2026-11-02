"""The generalised SANDBOX RELAXATION notice (P15 D6) and the unchanged prompt-root notice."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentteams.cli import write_root_policy as wrp
from agentteams.frameworks._prompt_root_protect import ALL_PROMPT_ROOT_EDIT_RULES
from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY
from agentteams.sandbox_state import SandboxExceptions, SandboxState


def _st(*, os: bool = True, wasm: bool = False, grants: dict | None = None, substrate: str | None = None,
        os_reason: str = "brief") -> SandboxState:
    return SandboxState(os=os, os_reason=os_reason, wasm=wasm, wasm_reason="cli" if wasm else "brief",
                        wasm_languages=(), wasm_grants=grants or {}, model_substrate=substrate)


def _g(*reads: str, scratch: bool = False):
    mounts = [SimpleNamespace(host=f"/p/{r}", guest=f"/project/{r}", mode="ro") for r in reads]
    if scratch:
        mounts.append(SimpleNamespace(host="/p/s", guest="/scratch", mode="rw"))
    return SimpleNamespace(mounts=tuple(mounts), limits=None)


def _lines(old, new, capsys, **kw) -> list[str]:
    lines = wrp.warn_sandbox_relaxation(old, new, framework_id=kw.pop("fw", "goose"), **kw)
    assert capsys.readouterr().err.splitlines() == lines
    assert all(line.startswith(wrp.SANDBOX_RELAXATION_PREFIX) for line in lines)
    return [line[len(wrp.SANDBOX_RELAXATION_PREFIX) + 1:] for line in lines]


def test_os_on_to_off(capsys):
    assert _lines(_st(), _st(os=False), capsys) == [
        "os sandbox OFF for goose (reason: brief); it was on. No OS boundary is emitted; a live block you merged "
        "earlier still applies until you remove it (run --check-wiring)."]


def test_os_off_by_operator_with_no_previous(capsys):
    assert _lines(None, _st(os=False, os_reason="cli"), capsys, fw="codex") == [
        "os sandbox OFF for codex by cli; the profile would emit one."]
    assert _lines(None, _st(os=False, os_reason="profile:cooperative"), capsys) == []  # today's behaviour


def test_wasm_and_substrate_off(capsys):
    assert _lines(_st(wasm=True, substrate="on"), _st(substrate="off"), capsys) == [
        "wasm sandbox OFF (reason: brief); it was on.", "model-substrate WASM switch OFF; it was on."]


def test_widening_lists_each_added_token(capsys):
    old = _st(wasm=True, grants={"a": _g("x")})
    new = _st(wasm=True, grants={"a": _g("x", "y", scratch=True)})
    assert _lines(old, new, capsys) == ["wasm grant widened: +wasm:a:read:y", "wasm grant widened: +wasm:a:scratch"]
    assert _lines(_st(), new, capsys) == [f"wasm grant widened: +{t}" for t in
                                         ("wasm:a:read:x", "wasm:a:read:y", "wasm:a:scratch")]


def test_keep_and_runner_switch(capsys):
    exc = (SandboxExceptions(wasm_programs=frozenset({"r1"})),
           SandboxExceptions(native_exec_keep=frozenset({"git-operations"}), os_programs=frozenset({"r1"})))
    assert _lines(_st(wasm=True), _st(wasm=True), capsys, exceptions=exc) == [
        "native exec kept for git-operations while wasm is on", "runner program for r1 moved from sandbox wasm to os"]


def test_nothing_continuing_prints(capsys):
    st = _st(os=False, wasm=True, grants={"a": _g("x")})
    assert _lines(st, st, capsys) == []


def test_enforce_calls_the_notice_only_when_given(tmp_path, capsys):
    manifest: dict = {}
    wrp.begin(manifest, tmp_path)
    common = dict(framework_id="claude", brief_roots=[], grant_roots=[], coordination_roots=[], accepted=None,
                  confined=False)
    wrp.enforce(manifest, **common)
    assert "SANDBOX RELAXATION" not in capsys.readouterr().err
    wrp.enforce(manifest, **common, sandbox_states=(_st(), _st(os=False)))
    assert "os sandbox OFF for claude" in capsys.readouterr().err


def test_prompt_root_notice_is_unchanged(tmp_path, capsys):
    rule = sorted(ALL_PROMPT_ROOT_EDIT_RULES)[0]
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"deny": [rule]}}))
    dropped = wrp.warn_prompt_root_relaxation({PROJECT_ROOT_KEY: Path(tmp_path)}, framework_id="claude",
                                              confined=False)
    assert dropped == [rule]
    assert capsys.readouterr().err == (
        "  !  SANDBOX RELAXATION: prompt-root protection removed: the live .claude/settings.json "
        f"permissions.deny carries {rule}, which the new render drops "
        "(protect_prompt_roots is off or absent in the brief, or the sandbox is off). Review "
        "before re-merging; the brief is agent-writable.\n")


@pytest.mark.parametrize("fw", ["claude", "goose", "codex", "grok", "copilot-vscode", "copilot-cli", "agents-md"])
def test_notice_prints_on_every_framework(fw, capsys):
    assert _lines(_st(), _st(os=False), capsys, fw=fw)
