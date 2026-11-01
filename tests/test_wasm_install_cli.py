"""CLI tests for --wasm-install / --wasm-status / --wasm-pin-update (P03). No external network."""

from __future__ import annotations

import io
import json
import shutil
import pytest

import build_team
from agentteams import wasm_runtime as wr
from agentteams.cli import wasm_commands
from agentteams.cli.parser import _build_parser
from tests.test_wasm_runtime import _api, _pin_env, _release, env  # noqa: F401 — fixture reuse


def _args(argv: list[str]):
    from agentteams.cli.parser import _validate_option_combinations
    parser = _build_parser()
    ns = parser.parse_args(argv)
    _validate_option_combinations(parser, ns)
    return ns


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_dry_run_lists_url_size_sha(capsys, tmp_path):
    rc = build_team.main(["--wasm-install", "javascript", "--dry-run", "--project", str(tmp_path)])
    out = capsys.readouterr().out
    pe = wr.pinned("javascript")["platforms"]["any"]
    assert rc == 0
    assert pe["url"] in out and str(pe["size"]) in out and pe["sha256"] in out
    assert not (tmp_path / ".agentteams").exists()


def test_install_todo_pin_component_exits_1(capsys, tmp_path, monkeypatch):
    doc = wr.load_pins()
    doc["components"]["wasmtime"]["platforms"]["darwin-arm64"]["files"]["wasmtime"]["sha256"] = wr.TODO_PIN
    pins = tmp_path / "pins-todo.json"
    pins.write_text(json.dumps(doc))
    monkeypatch.setattr(wr, "PINS_PATH", pins)
    monkeypatch.setattr(wr, "host_platform", lambda: "darwin-arm64")
    (tmp_path / "proj").mkdir()
    rc = build_team.main(["--wasm-install", "wasmtime", "--yes", "--project", str(tmp_path / "proj")])
    assert rc == 1 and "pin incomplete" in capsys.readouterr().err


def test_non_tty_without_yes_refuses(capsys, env, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = wasm_commands.run_wasm_install(_args(["--wasm-install", "javascript", "--project", str(env.project)]),
                                        fetch=env.fetch)
    assert rc == 2 and "--yes" in capsys.readouterr().err
    assert env.calls == []


def test_tty_answer_no_aborts(env, monkeypatch):
    monkeypatch.setattr("sys.stdin", _Tty(""))
    monkeypatch.setattr("builtins.input", lambda _q: "n")
    rc = wasm_commands.run_wasm_install(_args(["--wasm-install", "javascript", "--project", str(env.project)]),
                                        fetch=env.fetch)
    assert rc == 1 and env.calls == []


def test_install_then_status_exit_codes(capsys, env):
    ns = _args(["--wasm-status", "--project", str(env.project)])
    assert wasm_commands.run_wasm_status(ns) == 1
    rc = wasm_commands.run_wasm_install(
        _args(["--wasm-install", "javascript", "--yes", "--project", str(env.project)]), fetch=env.fetch)
    assert rc == 0 and "installed javascript" in capsys.readouterr().out
    assert wasm_commands.run_wasm_status(_args(["--wasm-status", "javascript", "--project", str(env.project)])) == 0
    assert wasm_commands.run_wasm_status(ns) == 1   # wasmtime/python still absent
    assert wasm_commands.run_wasm_status(_args(["--wasm-status", "ruby", "--project", str(env.project)])) == 2


def test_install_unknown_component_is_usage_error(capsys, tmp_path):
    assert build_team.main(["--wasm-install", "ruby", "--project", str(tmp_path)]) == 2


def test_network_failure_exits_3(capsys, env):
    def down(url, size, dest):
        raise wr.WasmNetworkError("connection refused")
    rc = wasm_commands.run_wasm_install(
        _args(["--wasm-install", "javascript", "--yes", "--project", str(env.project)]), fetch=down)
    assert rc == 3


@pytest.mark.parametrize("extra", [["--description", "x.json"], ["--self"], ["--update"], ["--output", "o"]])
@pytest.mark.parametrize("flag", [["--wasm-install"], ["--wasm-status"], ["--wasm-pin-update", "wasmtime", "1"]])
def test_flags_mutually_exclusive_with_generation(flag, extra):
    with pytest.raises(SystemExit) as ei:
        _args([*flag, *extra])
    assert ei.value.code == 2


def test_wasm_flags_mutually_exclusive_with_each_other():
    with pytest.raises(SystemExit):
        _args(["--wasm-install", "--wasm-status"])


@pytest.mark.parametrize("mod", [["--remediation", "GHSA-aaaa-bbbb-cccc"], ["--tofu"], ["--pins-out", "p.json"]])
def test_pin_update_modifiers_require_the_flag(mod):
    with pytest.raises(SystemExit):
        _args(["--wasm-install", *mod])


def test_pin_update_prints_diff_and_never_commits(capsys, env):
    assets, fetch = _pin_env(env)
    rel = _release("v1.0.0", "2020-01-01T00:00:00Z", assets)   # long past the cooldown on any clock
    ns = _args(["--wasm-pin-update", "wasmtime", "1.0.0", "--pins-out", str(env.pins), "--yes"])
    rc = wasm_commands.run_wasm_pin_update(ns, fetch=fetch, api_get=_api(rel, [rel]))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert out.startswith("--- a/pins.json") and "TODO-PIN" in out and "Not committed" in out
    assert "TODO-PIN" not in json.dumps(json.loads(env.pins.read_text())["components"]["wasmtime"])


def test_pin_update_requires_pins_out_outside_a_work_tree(capsys, monkeypatch):
    monkeypatch.setattr(wr, "default_pins_out", lambda: None)
    rc = wasm_commands.run_wasm_pin_update(_args(["--wasm-pin-update", "wasmtime", "36.0.20", "--yes"]))
    assert rc == 2 and "--pins-out" in capsys.readouterr().err


def test_default_pins_out_is_the_packaged_file_in_this_checkout():
    """This repository is a git work tree, so the packaged file is the default target."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    assert wr.default_pins_out() == wr.PINS_PATH


def test_pin_update_non_tty_without_yes_refuses(capsys, env, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    before = env.pins.read_bytes()
    rc = wasm_commands.run_wasm_pin_update(_args(["--wasm-pin-update", "wasmtime", "1.0.0",
                                                  "--pins-out", str(env.pins)]))
    assert rc == 2 and env.pins.read_bytes() == before


def test_pin_update_s10_refusal_exits_1(capsys, env):
    assets, fetch = _pin_env(env)
    young = _release("v1.0.0", "2099-01-01T00:00:00Z", assets)
    ns = _args(["--wasm-pin-update", "wasmtime", "1.0.0", "--pins-out", str(env.pins), "--yes"])
    assert wasm_commands.run_wasm_pin_update(ns, fetch=fetch, api_get=_api(young, [young])) == 1
    assert "cooldown" in capsys.readouterr().err


def test_flags_are_documented_in_the_parser_help():
    actions = {o for a in _build_parser()._actions for o in a.option_strings}
    assert {"--wasm-install", "--wasm-status", "--wasm-pin-update", "--remediation", "--tofu",
            "--pins-out"} <= actions


def test_dispatch_happens_before_any_render(monkeypatch, tmp_path):
    """The standalone dispatch returns before build_team's generation pipeline is imported or run."""
    import agentteams.cli.app as app

    def trap(*a, **k):
        raise AssertionError("generation pipeline reached")
    monkeypatch.setattr(app, "run_generate", trap)
    assert build_team.main(["--wasm-status", "--project", str(tmp_path)]) in (0, 1)
    assert not (tmp_path / ".agentteams").exists()
