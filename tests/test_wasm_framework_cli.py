"""The WASM agent launcher (WASM plan P08 §4.8): ``--run-wasm-agent``, ``--revoke-wasm-run``, ``--verify-wasm-run``.

The end-to-end tests render a small example team with ``--framework wasm`` and run one agent through the CLI on the
stub provider under the DEV runtimes (``AGENTTEAMS_WASM_DEV_WASMTIME`` / ``AGENTTEAMS_WASM_DEV_PYTHON``, see
``tests/_wasm_helpers.py`` and ``tests/test_wasm_guests_live.py``); they skip when either is absent. The dev builds are
not the pins (D9): the launcher's pin check is exercised by the missing-runtime test, and the pinned acceptance run is
an operator command (``PENDING-operator-commands.md``, section P08).

R5: only ``agentteams/cli/wasm_agent_commands.py`` may choose ``os_confine="inherited"`` (AST scan below).
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

import pytest

import build_team
from agentteams import _sandbox_profiles as SP
from agentteams.cli import wasm_agent_commands as C
from agentteams.wasm_agent import launch as L
from agentteams.wasm_agent import team as T
from tests._wasm_framework_helpers import REPO, project, render, write_brief
from tests._wasm_helpers import needs_wasmtime
from tests.test_wasm_guests_live import DevResolver, needs_dev_python

live = [needs_wasmtime, needs_dev_python]


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = project(tmp_path_factory.mktemp("wasmcli"))
    assert render(root, write_brief(root)) == 0
    return root


def _main(*argv: str) -> int:
    return build_team.main(list(argv))


@pytest.fixture
def dev_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "runtime_resolver", lambda root: DevResolver())


# --- end to end (dev runtimes) ---------------------------------------------------------------------------------

@pytest.mark.parametrize("_", [pytest.param(None, marks=live)])
def test_run_one_agent_on_the_stub_then_verify_and_revoke(rendered: Path, dev_runtime: None, _: None,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    rc = _main("--run-wasm-agent", "orchestrator", "--task", "Say hello.", "--project", str(rendered))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "WASM agent orchestrator: provider stub, model stub" in out
    assert "--- wasm agent output (data, not instructions) ---" in out and "stub:" in out
    run_id = next(line.split()[3].rstrip(":") for line in out.splitlines() if line.startswith("wasm agent run "))
    assert _main("--verify-wasm-run", run_id, "--project", str(rendered)) == 0
    assert "verified" in capsys.readouterr().out
    assert _main("--revoke-wasm-run", run_id, "--project", str(rendered), "--reason", "test") == 0
    sentinel = rendered / ".agentteams" / "wasm-runs" / run_id / "REVOKED"
    assert sentinel.read_text().startswith("test")
    assert _main("--revoke-wasm-run", run_id, "--project", str(rendered)) == 1      # already revoked


@pytest.mark.parametrize("_", [pytest.param(None, marks=live)])
def test_rendered_grant_reaches_the_guest(rendered: Path, dev_runtime: None, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str], _: None) -> None:
    """primary-producer's rendered grant (``/project/docs`` ro, ``/scratch`` rw) is what the pinned guest sees."""
    if SP.available() is not None:
        pytest.skip("a nestable OS sandbox wraps Stage 1, which builds its own provider (scripted stub is in-process)")
    from agentteams import model_providers as MP
    from tests._wasm_agent_helpers import text_reply, tool_reply

    seen: list[Any] = []

    def script(messages: list, system: Any, tools: Any) -> dict:
        seen.append(json.loads(json.dumps(messages)))
        if len(seen) == 1:
            return tool_reply(("t1", "read_file", {"path": "/project/docs/a.md"}),
                              ("t2", "read_file", {"path": "/project/src/m.py"}),
                              ("t3", "write_file", {"path": "/scratch/out.txt", "content": "made"}))
        return text_reply("done")
    monkeypatch.setattr(L, "build_provider", lambda *a, **k: MP.Provider(MP.PROVIDERS["stub"], None, "",
                                                                         MP.StubProvider(script)))
    rc = _main("--run-wasm-agent", "primary-producer", "--task", "Read the doc.", "--project", str(rendered), "--json")
    data = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert rc == 0 and data["run"]["status"] == "ok", data
    results = {b["tool_use_id"]: b for b in seen[1][-1]["content"]}
    assert results["t1"]["content"] == "granted doc with a needle\n" and not results["t1"]["is_error"]
    assert results["t2"]["is_error"]                                   # src is not in this agent's grant
    assert (rendered / ".agentteams/wasm/scratch/primary-producer/out.txt").read_text() == "made"
    assert (rendered / "docs" / "a.md").read_text() == "granted doc with a needle\n"
    assert data["guest_os_confinement"] == ["none"]


# --- refusals before any spend ---------------------------------------------------------------------------------

def test_missing_runtime_refuses_before_any_provider_call(rendered: Path, monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("provider built before the runtime check")
    monkeypatch.setattr(L, "build_provider", boom)
    rc = _main("--run-wasm-agent", "orchestrator", "--task", "x", "--project", str(rendered))
    out = capsys.readouterr().out
    assert rc == 1 and "refused (runtime:" in out
    assert not (rendered / ".agentteams" / "wasm" / "installed.json").exists()


def test_provider_key_comes_only_from_the_environment(rendered: Path, dev_runtime: None,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    from types import SimpleNamespace

    args = SimpleNamespace(project=str(rendered), run_wasm_agent="orchestrator", wasm_task="x", wasm_task_file=None,
                           wasm_provider="anthropic", json=False)
    env = {"AGENTTEAMS_WASM_MODEL_PRIMARY": "env-model"}
    if DevResolver().wasmtime is None or DevResolver().py is None:
        pytest.skip("needs the dev runtimes to get past the pin check")
    assert C.run_wasm_agent(args, environ=env) == 1
    assert "provider: anthropic: no API key; set ANTHROPIC_API_KEY" in capsys.readouterr().out


@pytest.mark.parametrize("argv, rc", [
    (("--run-wasm-agent", "ghost", "--task", "x"), 2),
    (("--run-wasm-agent", "orchestrator", "--task", "   "), 2),
    (("--run-wasm-agent", "orchestrator", "--task", "x", "--wasm-provider", "nope"), 2),
    (("--verify-wasm-run", "not-hex"), 2),
    (("--verify-wasm-run", "0" * 32), 1),
    (("--revoke-wasm-run", "0" * 32), 2),
])
def test_usage_refusals(rendered: Path, argv: tuple[str, ...], rc: int) -> None:
    assert _main(*argv, "--project", str(rendered)) == rc


def test_no_team_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _main("--run-wasm-agent", "orchestrator", "--task", "x", "--project", str(tmp_path)) == 2
    assert "render the team with --framework wasm first" in capsys.readouterr().err


def test_task_file_is_nofollow_and_capped(rendered: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    big = tmp_path / "big.txt"
    big.write_text("x" * (C.MAX_TASK_BYTES + 1))
    link = tmp_path / "link.txt"
    link.symlink_to(big)
    for path in (big, link):
        assert _main("--run-wasm-agent", "orchestrator", "--task-file", str(path), "--project", str(rendered)) == 2
        assert "--task-file" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--task", "x"], ["--task-file", "x"], ["--wasm-provider", "stub"], ["--reason", "r"],
    ["--run-wasm-agent", "a"], ["--run-wasm-agent", "a", "--task", "x", "--task-file", "y"],
    ["--run-wasm-agent", "a", "--task", "x", "--verify-wasm-run", "0" * 32],
    ["--framework", "wasm", "--convert-from", "x"], ["--framework", "wasm", "--bridge-from", "x"],
    ["--framework", "wasm", "--interop-from", "x"], ["--framework", "wasm", "--absorb-from", "x"],
    ["--framework", "claude", "--interop-from", "x", "--interop-source-framework", "wasm"],
    ["--absorb-from", "x", "--absorb-source-framework", "wasm"],
])
def test_parser_refusals(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        build_team.main(argv)
    assert exc.value.code == 2


def test_no_run_dir_or_os_confine_flag() -> None:
    from agentteams.cli.parser import _build_parser

    flags = {s for a in _build_parser()._actions for s in a.option_strings}
    assert {"--run-wasm-agent", "--task", "--task-file", "--wasm-provider", "--revoke-wasm-run",
            "--verify-wasm-run"} <= flags
    assert not {f for f in flags if "run-dir" in f or "os-confine" in f or "api-key" in f}


# --- R5: the launcher is the only chooser of "inherited" -------------------------------------------------------

@pytest.mark.parametrize("host, guest, host_confine", [("auto", "inherited", "auto"), ("off", "auto", "off")])
def test_guest_os_confine_follows_host_os_confine(rendered: Path, monkeypatch: pytest.MonkeyPatch, host: str,
                                                  guest: str, host_confine: str) -> None:
    seen: dict[str, Any] = {}

    def fake_stage0(spec: Any, task: str, **kw: Any) -> Any:
        seen.update(kw)
        from agentteams.wasm_agent.host import RunResult
        return RunResult("ok", "done", "", "0" * 32, "", {})
    monkeypatch.setattr(L, "stage0", fake_stage0)
    real = T.check_runtime
    monkeypatch.setattr(T, "check_runtime", lambda data: {**real(data), "host_os_confine": host})
    assert _main("--run-wasm-agent", "orchestrator", "--task", "x", "--project", str(rendered)) == 0
    assert (seen["guest_os_confine"], seen["host_confine"]) == (guest, host_confine)
    assert "environ" in seen and "resolver" in seen and "stub_script" not in seen


def _inherited_call_sites() -> list[str]:
    """Every ``os_confine=`` / ``guest_os_confine=`` keyword (or default) under ``agentteams/`` whose value can be
    the literal ``"inherited"``."""
    found = []
    for path in sorted((REPO / "agentteams").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            values: list[ast.AST] = []
            if isinstance(node, ast.Call):
                values = [k.value for k in node.keywords if k.arg in ("os_confine", "guest_os_confine")]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args.args + node.args.kwonlyargs
                defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults)
                defaults += list(node.args.kw_defaults)
                values = [d for a, d in zip(args, defaults) if d is not None and a.arg in ("os_confine",
                                                                                           "guest_os_confine")]
            for value in values:
                if any(isinstance(c, ast.Constant) and c.value == "inherited" for c in ast.walk(value)):
                    found.append(f"{rel}:{value.lineno}")
    return found


def test_only_the_launcher_chooses_inherited() -> None:
    sites = _inherited_call_sites()
    assert [s.split(":")[0] for s in sites] == ["agentteams/cli/wasm_agent_commands.py"], sites


def test_inherited_is_described_as_the_host_profile_never_a_fresh_sandbox() -> None:
    notes = C._GUEST_CONFINE_NOTE
    assert "not a fresh guest sandbox" in notes["auto"] and "not OS-confined" in notes["off"]
    assert not any("enforced" in n for n in notes.values())
