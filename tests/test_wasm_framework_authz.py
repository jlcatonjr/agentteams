"""The P08 review remediation (W8R): who chooses the base URL (@security H1), the launch-time grant authorization
(C2), the locked price table (C3) and the hygiene items around them.

H1 (S5): the provider key is sent to the base URL, so a keyed preset's URL comes only from the operator at run time
(``AGENTTEAMS_WASM_BASE_URL`` / ``--wasm-base-url``). The brief may name one only for a keyless preset on a loopback
host, and ``--wasm-provider`` re-applies that rule. No test here lets a key travel to a host its preset does not name.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentteams import model_providers as MP
from agentteams.cli import sandbox_relaxation_gate as gate
from agentteams.cli import wasm_agent_commands as C
from agentteams.wasm_agent import launch as L
from agentteams.wasm_agent import team as T
from tests._sandbox_gate_helpers import _log, _row, _team
from tests._wasm_framework_helpers import project, render, write_brief

_KEYED = ("anthropic", "openai", "openrouter", "gemini", "xai", "openai-compatible")
_T0 = datetime(2026, 11, 1, tzinfo=UTC)


def _render(root: Path, **overrides: Any) -> int:
    """Render, turning the lock step's SystemExit into its exit code."""
    try:
        return render(root, write_brief(root, **overrides))
    except SystemExit as exc:
        return int(exc.code or 0)


@pytest.fixture(scope="module")
def ollama_team(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An ungoverned team whose brief names a keyless local server on a loopback host."""
    root = project(tmp_path_factory.mktemp("wasmurl"))
    assert _render(root, enforce_decision_signing=False, wasm_runtime={
        "provider": "ollama", "base_url": "http://127.0.0.1:9999/v1", "models": {"primary": "m", "cheap": "m"}}) == 0
    return root


@pytest.fixture(scope="module")
def governed_team(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A governed team (the render turns enforce_decision_signing on) with block limits above the defaults."""
    root = project(tmp_path_factory.mktemp("wasmgov"))
    brief = json.loads(write_brief(root).read_text())
    brief["wasm_sandbox"]["limits"] = {"timeout_s": 200}
    assert _render(root, wasm_sandbox=brief["wasm_sandbox"]) == 0
    return root


@pytest.fixture
def copy_of(tmp_path: Path):
    def _copy(src: Path) -> Path:
        root = Path(os.path.realpath(tmp_path)) / "proj"
        shutil.copytree(src, root, symlinks=True)
        return root
    return _copy


def _fake_stage0(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(spec: Any, task: str, **kw: Any) -> Any:
        from agentteams.wasm_agent.host import RunResult
        seen.update(kw, provider=spec.provider)
        return RunResult("ok", "done", "", "0" * 32, "", {})
    monkeypatch.setattr(L, "stage0", fake)
    return seen


def _args(root: Path, slug: str = "orchestrator", **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(project=str(root), run_wasm_agent=slug, wasm_task="x", wasm_task_file=None,
                           wasm_provider=kw.pop("provider", None), wasm_base_url=kw.pop("url", None), json=False, **kw)


def _key_goes_only_to_its_preset(seen: dict[str, Any], operator: str | None = None) -> None:
    """The invariant every launcher test ends on: a keyed preset gets its own URL (None) or the operator's."""
    if MP.preset_needs_key(seen["provider"]):
        assert seen["base_url"] in (None, operator), seen


# --- H1: the brief may not choose where a key goes ------------------------------------------------------------

@pytest.mark.parametrize("provider", _KEYED)
@pytest.mark.parametrize("url", ["https://api.example.com/v1", "http://127.0.0.1:8080/v1"])
def test_brief_base_url_for_a_keyed_preset_is_refused(provider: str, url: str) -> None:
    with pytest.raises(ValueError, match="base_url is refused.*operator"):
        T.normalize_wasm_runtime({"provider": provider, "base_url": url})


@pytest.mark.parametrize("block", [
    {"base_url": "http://127.0.0.1:11434/v1"},                       # no provider: --wasm-provider could pick any
    {"provider": "stub", "base_url": "http://127.0.0.1:1/v1"},
    {"provider": "ollama", "base_url": "https://models.example.com/v1"},   # keyless, but not loopback
    {"provider": "lmstudio", "base_url": "http://u:p@127.0.0.1:1/v1"},
    {"provider": "ollama", "base_url": 7},
])
def test_brief_base_url_outside_the_keyless_loopback_rule_is_refused(block: dict) -> None:
    with pytest.raises(ValueError, match="base_url"):
        T.normalize_wasm_runtime(block)


@pytest.mark.parametrize("provider, url", [("ollama", "http://127.0.0.1:11434/v1/"),
                                           ("lmstudio", "http://localhost:1234/v1"), ("ollama", "http://[::1]:11434/v1")])
def test_keyless_loopback_brief_url_is_allowed(provider: str, url: str) -> None:
    got = T.normalize_wasm_runtime({"provider": provider, "base_url": url})["base_url"]
    assert got == url.rstrip("/")


def test_render_fails_closed_on_a_keyed_brief_url(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    rc = _render(root, wasm_runtime={"provider": "anthropic", "base_url": "https://collector.example/v1"})
    assert rc != 0 and "base_url is refused" in capsys.readouterr().err
    assert not (root / T.LOCK_REL).exists() and not (root / T.RUNTIME_REL).exists()


@pytest.mark.parametrize("provider, kept", [("ollama", True), ("lmstudio", True), ("anthropic", False),
                                            ("openai", False), ("openrouter", False), ("gemini", False)])
def test_run_time_preset_switch_re_applies_the_rule(provider: str, kept: bool) -> None:
    runtime = {"provider": "ollama", "base_url": "http://127.0.0.1:9999/v1"}
    url, notice = T.resolve_base_url(runtime, provider, operator_url=None)
    assert (url, notice is None) == (("http://127.0.0.1:9999/v1", True) if kept else (None, False))


def test_operator_url_wins_and_is_still_shape_checked() -> None:
    runtime = {"provider": "ollama", "base_url": "http://127.0.0.1:9999/v1"}
    assert T.resolve_base_url(runtime, "anthropic", operator_url="https://proxy.corp/v1/") == ("https://proxy.corp/v1",
                                                                                               None)
    for bad in ("http://proxy.corp/v1", "https://k@proxy.corp/v1", "https://proxy.corp/v1?x=1"):
        with pytest.raises(T.TeamError, match="operator base URL"):
            T.resolve_base_url(runtime, "anthropic", operator_url=bad)
    with pytest.raises(T.TeamError, match="stub"):
        T.resolve_base_url({}, "stub", operator_url="https://proxy.corp/v1")


def test_a_hand_edited_runtime_json_url_is_refused_at_load(copy_of, ollama_team: Path) -> None:
    root = copy_of(ollama_team)
    path = root / T.RUNTIME_REL
    data = json.loads(path.read_text())
    path.write_text(T.dumps({**data, "provider": "openai"}))
    T.write_lock(root)                                   # a writer who also controls the control plane
    with pytest.raises(T.TeamError, match="base_url is refused"):
        T.load_team(root)


def test_launcher_drops_the_rendered_url_for_a_keyed_preset(ollama_team: Path, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    seen = _fake_stage0(monkeypatch)
    env = {"ANTHROPIC_API_KEY": "sk-test", "AGENTTEAMS_WASM_MODEL_PRIMARY": "m"}
    assert C.run_wasm_agent(_args(ollama_team, provider="anthropic"), environ=env) == 0
    assert seen["base_url"] is None and seen["provider"] == "anthropic"
    assert "rendered wasm_runtime.base_url is not used for provider 'anthropic'" in capsys.readouterr().err
    _key_goes_only_to_its_preset(seen)


def test_launcher_keeps_the_rendered_keyless_loopback_url(ollama_team: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_stage0(monkeypatch)
    assert C.run_wasm_agent(_args(ollama_team), environ={}) == 0
    assert (seen["provider"], seen["base_url"]) == ("ollama", "http://127.0.0.1:9999/v1")


@pytest.mark.parametrize("via", ["env", "flag", "both"])
def test_launcher_takes_the_operator_url_from_env_or_flag(ollama_team: Path, monkeypatch: pytest.MonkeyPatch,
                                                          via: str) -> None:
    seen = _fake_stage0(monkeypatch)
    env = {"OPENAI_API_KEY": "sk-test", "AGENTTEAMS_WASM_MODEL_PRIMARY": "m"}
    if via in ("env", "both"):
        env[T.BASE_URL_ENV] = "https://env.corp/v1"
    flag = "https://flag.corp/v1" if via in ("flag", "both") else None
    assert C.run_wasm_agent(_args(ollama_team, provider="openai", url=flag), environ=env) == 0
    want = "https://env.corp/v1" if via == "env" else "https://flag.corp/v1"
    assert seen["base_url"] == want
    _key_goes_only_to_its_preset(seen, operator=want)


def test_launcher_refuses_a_bad_operator_url_before_any_run(ollama_team: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    seen = _fake_stage0(monkeypatch)
    env = {T.BASE_URL_ENV: "http://collector.example/v1", "OPENAI_API_KEY": "k", "AGENTTEAMS_WASM_MODEL_PRIMARY": "m"}
    assert C.run_wasm_agent(_args(ollama_team, provider="openai"), environ=env) == C.EXIT_USAGE
    assert not seen and "operator base URL" in capsys.readouterr().err


def test_base_url_flag_needs_the_launcher() -> None:
    import build_team

    with pytest.raises(SystemExit) as exc:
        build_team.main(["--wasm-base-url", "https://x.example/v1"])
    assert exc.value.code == 2


# --- C2: the launch-time authorization check -------------------------------------------------------------------

def test_ungoverned_widened_grant_runs_with_a_notice(ollama_team: Path, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    _fake_stage0(monkeypatch)
    assert C.run_wasm_agent(_args(ollama_team, "primary-producer"), environ={}) == 0
    err = capsys.readouterr().err
    assert "SANDBOX RELAXATION: primary-producer" in err and "wasm:primary-producer:read:docs" in err


def test_compute_only_agent_needs_no_authorization(ollama_team: Path, capsys: pytest.CaptureFixture[str]) -> None:
    C.authorize_grant(T.load_team(ollama_team), "orchestrator")
    assert "SANDBOX RELAXATION" not in capsys.readouterr().err


def test_governed_widened_grant_without_an_exception_is_refused(governed_team: Path, monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    seen = _fake_stage0(monkeypatch)
    assert C.run_wasm_agent(_args(governed_team, "primary-producer"), environ={}) == C.EXIT_FAILED
    err = capsys.readouterr().err
    assert not seen and "no recorded authorization" in err and "wasm-grant-widen:wasm" in err


def test_governed_widened_grant_with_an_active_exception_runs(governed_team: Path, monkeypatch: pytest.MonkeyPatch
                                                              ) -> None:
    calls: list[tuple] = []

    def standing(team_dir: Path, kind: str, qualifier: str, grants: tuple = (), *, now: Any = None) -> dict:
        calls.append((team_dir, kind, qualifier, grants))
        return {"authorization": "active-exception"}
    monkeypatch.setattr(gate, "standing_authorization", standing)
    seen = _fake_stage0(monkeypatch)
    assert C.run_wasm_agent(_args(governed_team, "primary-producer"), environ={}) == 0 and seen
    assert calls == [(governed_team / T.AGENTS_REL, "wasm-grant-widen", "wasm",
                      ("wasm:primary-producer:read:docs", "wasm:primary-producer:scratch"))]


def test_standing_authorization_is_the_read_only_carry_over_check(tmp_path: Path, stub_ed25519: None) -> None:
    team = _team(tmp_path)
    tokens = ("wasm:analyst:read:data", "wasm:analyst:scratch")
    with pytest.raises(gate.SandboxRelaxationError, match="no exception records"):
        gate.standing_authorization(team, "wasm-grant-widen", "wasm", tokens, now=_T0)
    _log(team, _row("wasm-grant-widen:wasm", grants=";".join(tokens)))
    gate.authorize(team, [gate.Relaxation("wasm-grant-widen", "wasm", grants=tokens)], consume=True, now=_T0)
    registry = (team / "references" / "exception-registry.json").read_bytes()
    log = (team / "references" / "security-decisions.log.csv").read_bytes()
    rec = gate.standing_authorization(team, "wasm-grant-widen", "wasm", tokens, now=_T0)
    assert rec["authorization"] == "active-exception" and rec["exception_id"].startswith("wasm-grant-widen:wasm@")
    assert (team / "references" / "exception-registry.json").read_bytes() == registry   # consumed nothing
    assert (team / "references" / "security-decisions.log.csv").read_bytes() == log
    with pytest.raises(gate.SandboxRelaxationError, match="no exception records"):
        gate.standing_authorization(team, "wasm-grant-widen", "wasm", ("wasm:analyst:read:secrets",), now=_T0)
    with pytest.raises(gate.SandboxRelaxationError, match="lapsed"):
        gate.standing_authorization(team, "wasm-grant-widen", "wasm", tokens, now=datetime(2027, 6, 1, tzinfo=UTC))


def test_governed_raised_limits_must_match_the_recorded_state(copy_of, governed_team: Path) -> None:
    root = copy_of(governed_team)
    team = T.load_team(root)
    assert team.grants["orchestrator"].limits.timeout_s == 200
    C.authorize_grant(team, "orchestrator")                     # matches the render's recorded sandbox_state
    path = root / T.GRANTS_REL
    doc = json.loads(path.read_text())
    doc["agents"]["orchestrator"]["limits"]["timeout_s"] = 300
    path.write_text(T.dumps(doc))
    T.write_lock(root)                                          # a writer who also controls the control plane
    with pytest.raises(T.TeamError, match="do not match the ones the render's sandbox gate recorded"):
        C.authorize_grant(T.load_team(root), "orchestrator")


def test_load_re_runs_the_credential_scan(copy_of, ollama_team: Path) -> None:
    root = copy_of(ollama_team)
    T.load_team(root)
    (root / "docs" / ".env").write_text("TOKEN=x\n")             # planted after the render
    with pytest.raises(T.TeamError, match="credential"):
        T.load_team(root)


def test_a_credential_named_read_path_is_refused(copy_of, ollama_team: Path) -> None:
    root = copy_of(ollama_team)
    (root / ".ssh").mkdir()
    path = root / T.GRANTS_REL
    doc = json.loads(path.read_text())
    doc["agents"]["primary-producer"]["mounts"].insert(0, {"host": ".ssh", "guest": "/project/.ssh", "mode": "ro"})
    path.write_text(T.dumps(doc))
    T.write_lock(root)
    with pytest.raises(T.TeamError, match="credential-named"):
        T.load_team(root)


@pytest.mark.parametrize("limits", [None, [], "x"])
def test_non_object_limits_refuse_before_the_grant_build(limits: Any, tmp_path: Path) -> None:
    entry = {"mounts": [], "limits": limits, "read_only_via": "none"}
    with pytest.raises(T.TeamError, match="limits must be an object"):
        T.grant_from_entry("a", entry, project_root=tmp_path)


# --- C3: the price table is locked -----------------------------------------------------------------------------

_PRICES = {"m": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}}


def test_prices_file_is_hashed_into_the_lock(tmp_path: Path, copy_of) -> None:
    src = project(tmp_path / "src")
    (src / "prices.json").write_text(json.dumps(_PRICES))
    assert _render(src, enforce_decision_signing=False,
                   wasm_runtime={"provider": "stub", "prices_file": "prices.json"}) == 0
    lock = json.loads((src / T.LOCK_REL).read_text())
    assert lock["prices"]["path"] == "prices.json" and len(lock["prices"]["sha256"]) == 64
    assert T.load_team(src).prices()["m"]["output_per_mtok"] == 2.0
    root = copy_of(src)
    (root / "prices.json").write_text(json.dumps({"m": {"input_per_mtok": 0.0, "output_per_mtok": 0.0}}))
    with pytest.raises(T.TeamError, match=r"prices\.json \(prices changed\)"):
        T.load_team(root)
    (root / "prices.json").unlink()
    (root / "prices.json").symlink_to(src / "prices.json")
    with pytest.raises(T.TeamError, match="prices.json"):
        T.load_team(root)


def test_a_missing_prices_file_fails_the_render(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """H-c: a lock that cannot be written makes the render exit non-zero."""
    root = project(tmp_path)
    assert _render(root, enforce_decision_signing=False,
                   wasm_runtime={"provider": "stub", "prices_file": "missing.json"}) == 1
    assert "Error: WASM team lock not written" in capsys.readouterr().err
    assert not (root / T.LOCK_REL).exists()


# --- hygiene ---------------------------------------------------------------------------------------------------

def test_unreadable_transcript_is_reported_not_silently_empty(tmp_path: Path) -> None:
    import io

    err = io.StringIO()
    assert C.guest_confinement(str(tmp_path), err) == []          # a directory: read_text raises
    assert "guest os_confinement report unavailable" in err.getvalue()


def test_adapter_state_cache_is_keyed_on_the_manifest_object(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.frameworks import wasm as FW

    made: list[int] = []
    monkeypatch.setattr(FW, "wasm_team_state", lambda m: made.append(1) or object())
    adapter = FW.WasmAdapter()
    first = {"framework": "wasm"}
    assert adapter._team_state(first) is adapter._team_state(first) and len(made) == 1
    adapter._team_state({"framework": "wasm"})
    assert len(made) == 2


def test_lock_hook_is_a_no_op_for_other_frameworks(tmp_path: Path) -> None:
    from agentteams.frameworks.wasm import lock_wasm_team

    assert lock_wasm_team({"framework": "claude"}, tmp_path) is None


def test_renamed_launcher_flags() -> None:
    from agentteams.cli.parser import _build_parser

    flags = {s for a in _build_parser()._actions for s in a.option_strings}
    assert {"--wasm-task", "--wasm-task-file", "--wasm-base-url", "--revoke-reason"} <= flags
    assert not {"--task", "--task-file", "--reason"} & flags
    dests = {a.dest for a in _build_parser()._actions}
    assert "wasm_reason" not in dests and "revoke_reason" in dests
