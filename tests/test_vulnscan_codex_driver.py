"""The Codex CLI runtime driver (vulnscan-agent-runtimes plan, phase 4; D4 residual opt-in).

Docker-free and offline: argv, env, ``-c`` overrides (parsed as TOML) and gateway wiring are pure functions;
the self-test's headless run is exercised against a fake ``codex`` (a small Python script) talking to the
loopback stub, so the offered-tool inventory, the canary and the residual ``apply_patch`` judgement are tested
without the real CLI or any network.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

from agentteams.vulnscan import (
    agent_drivers,
    agent_drivers_codex,
    agent_run,
    debrief,
    executor,
    gateway_dialects,
    mcp,
    selftest_canary,
    selftest_cli,
)

RUN = Path("/state/2026-10-22")
SESSION = "vulnscan-2026-10-22"
MODEL = "openai/gpt-5.5"
CODEX = agent_drivers.CODEX
OK = {"framework": "codex", "model": MODEL, "accept_residual_tools": ["apply_patch"]}


@pytest.fixture
def codex_cfg(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(agent_run, "team_dir_problem", lambda *a, **k: None)
    monkeypatch.setattr(executor, "spool_dir", lambda: Path("/spool"))
    return {**agent_run.DEFAULT_AGENT, **OK, "team_dir": "/team", "key_file": "/keys/openrouter.env"}


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def _overrides(argv: list[str]) -> dict:
    """Parse every ``-c key=value`` as TOML (the CLI parses the value the same way)."""
    out = {}
    for i, part in enumerate(argv):
        if part == "-c":
            key, value = argv[i + 1].split("=", 1)
            out[key] = tomllib.loads(f"v = {value}")["v"]
    return out


# ---------------------------------------------------------------- env, command, MCP wiring

def test_the_container_env_holds_only_the_dummy_key() -> None:
    env = CODEX.env({}, "http://gw:8080")
    assert env["OPENAI_API_KEY"] == agent_drivers.DUMMY_KEY and env["OPENAI_BASE_URL"] == "http://gw:8080/v1"
    assert env["CODEX_HOME"] == agent_drivers.AGENT_HOME          # the tmpfs home: empty at every start
    assert CODEX.state_dirs() == [agent_drivers_codex.CODEX_STATE]
    assert agent_drivers_codex.CODEX_STATE.startswith(agent_drivers.AGENT_HOME + "/")
    cfg = _overrides(CODEX.command({"model": MODEL}, recipe="/r", session="s", task="t", run_id="r"))
    assert cfg["log_dir"].startswith(agent_drivers_codex.CODEX_STATE + "/")      # only logs and state DB persist
    assert cfg["sqlite_home"].startswith(agent_drivers_codex.CODEX_STATE + "/")
    assert CODEX.key_env == "OPENAI_API_KEY" and CODEX.model_env == "" and CODEX.run_env(OK) == {}


def test_the_gateway_url_in_the_command_is_the_run_gateway() -> None:
    assert agent_drivers_codex.GATEWAY_URL == f"http://{agent_run.GATEWAY}:{agent_run.GW_PORT}"


def test_the_command_locks_codex_down() -> None:
    argv = CODEX.command({"model": MODEL}, recipe="/work/team/a.toml", session=SESSION, task="TASK", run_id="R1")
    assert argv[:2] == ["codex", "exec"] and argv[-1] == "TASK"            # flags first, the prompt last
    for flag in ("--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
                 "--strict-config"):
        assert flag in argv, flag
    assert _flag(argv, "-s") == "read-only" and _flag(argv, "-m") == MODEL and _flag(argv, "--color") == "never"
    disabled = {argv[i + 1] for i, a in enumerate(argv) if a == "--disable"}
    assert disabled == set(agent_drivers_codex.DISABLED_FEATURES)
    assert {"shell_tool", "unified_exec", "multi_agent", "apps", "plugins", "hooks", "view_image",
            "enable_request_compression", "api_key_model_discovery"} <= disabled
    cfg = _overrides(argv)
    assert cfg["approval_policy"] == "never" and cfg["model_provider"] == "vulnscan_gateway"
    provider = cfg["model_providers.vulnscan_gateway"]
    assert provider["base_url"] == agent_drivers_codex.GATEWAY_URL + "/v1" and provider["wire_api"] == "responses"
    assert provider["env_key"] == "OPENAI_API_KEY" and provider["supports_websockets"] is False
    assert provider["capabilities"] == {"external_web_access": False, "remote_compaction": "unsupported"}
    server = cfg["mcp_servers.vulnscan"]
    assert server["command"] == agent_drivers.PYTHON and server["args"] == ["-m", "agentteams.vulnscan.mcp"]
    assert server["env"] == agent_drivers.MCP_ENV and server["required"] is True
    assert server["enabled_tools"] == sorted(mcp.SCHEMAS)
    assert [k for k in cfg if k.startswith("mcp_servers.")] == ["mcp_servers.vulnscan"]
    assert cfg["model_instructions_file"] == "/work/team/a.toml" and cfg["project_doc_max_bytes"] == 0
    assert cfg["web_search"] == "disabled" and cfg["history.persistence"] == "none" and cfg["notify"] == []
    assert cfg["tools.update_plan.enabled"] is False and cfg["agents.enabled"] is False
    assert CODEX.mcp_attach().kind == "codex-config-override"
    assert CODEX.command_template()[CODEX.command_template().index("-m") + 1] == "{model}"


def test_toml_values_escape_strings() -> None:
    value = agent_drivers_codex.toml_value({"a": 'x"y\\z', "b": [1, True], "c": {"d": "e"}})
    assert tomllib.loads(f"v = {value}")["v"] == {"a": 'x"y\\z', "b": [1, True], "c": {"d": "e"}}
    with pytest.raises(TypeError):
        agent_drivers_codex.toml_value(None)


def test_agent_argv_for_codex(codex_cfg: dict) -> None:
    argv = agent_run.agent_argv(codex_cfg, "img:x", RUN, SESSION, task="TASK TEXT", run_id="R1")
    image = argv.index("img:x")
    head, cmd = argv[:image], argv[image + 1:]
    assert f"/state/2026-10-22/agent/codex-data:{agent_drivers_codex.CODEX_STATE}:rw" in head
    assert "OPENAI_API_KEY=injected-by-gateway" in head and ["-w", "/work/cwd"] == head[head.index("-w"):head.index("-w") + 2]
    assert _overrides(cmd)["model_instructions_file"] == "/work/team/.codex/agents/vuln-remediation.toml"
    assert cmd[-1] == "TASK TEXT" and _flag(cmd, "-m") == MODEL


def test_codex_has_no_debrief(codex_cfg: dict, tmp_path: Path) -> None:
    assert CODEX.debrief() is None
    a = {**codex_cfg, "team_dir": str(tmp_path)}
    assert debrief._preflight({"agent": a}, a, tmp_path) == "no debrief for codex"


# ---------------------------------------------------------------- outcome parsing

def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_parse_outcome() -> None:
    p = CODEX.parse_outcome
    warn = {"type": "item.completed", "item": {"type": "error", "message": "Model metadata not found"}}
    assert p(_stream({"type": "thread.started"}, warn, {"type": "turn.completed", "usage": {}})) == agent_drivers.Outcome("")
    out = p(_stream({"type": "turn.failed", "error": {"message": "HTTP 400: refused"}}))
    assert out.error == "codex turn failed: HTTP 400: refused" and out.model_calls is None
    assert p(_stream({"type": "error", "message": "stream disconnected"})).error == "codex error: stream disconnected"
    assert p(_stream(warn)).error == "Model metadata not found"
    assert p("Error: thread/start failed\n").error == "Error: thread/start failed"
    assert p("").error == "codex printed no turn.completed event"
    assert len(p(_stream({"type": "turn.failed", "error": "x" * 900})).error) == 300


# ---------------------------------------------------------------- gateway wiring (responses)

def test_gateway_argv_for_codex_via_openrouter(codex_cfg: dict) -> None:
    argv = agent_run.gateway_argv(codex_cfg, "img:x", Path("/spend"))
    assert "/keys/openrouter.env:/run/secrets/responses.env:ro" in argv
    assert _flag(argv, "--dialects") == "responses" and _flag(argv, "--model") == MODEL
    assert _flag(argv, "--upstream-responses") == "https://openrouter.ai/api/v1"
    assert _flag(argv, "--key-file-responses") == "/run/secrets/responses.env"
    assert _flag(argv, "--max-output-tokens") == _flag(argv, "--responses-reserve-tokens") == "32000"
    assert "--upstream" not in argv and "--key-file" not in argv and "--anthropic-betas" not in argv


def test_gateway_argv_for_codex_via_openai(codex_cfg: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    a = {**codex_cfg, "model": "gpt-5.5", "upstream_responses": "https://api.openai.com/v1", "prices": "/p.json"}
    argv = agent_run.gateway_argv(a, "img:x", Path("/spend"))
    assert f"{tmp_path}/.config/agentteams-secrets/openai.env:/run/secrets/responses.env:ro" in argv
    assert "/p.json:/run/secrets/prices.json:ro" in argv
    explicit = agent_run.gateway_argv({**a, "key_file_responses": "/k/o.env"}, "img:x", Path("/spend"))
    assert "/k/o.env:/run/secrets/responses.env:ro" in explicit


# ---------------------------------------------------------------- validation (D2, D4) and override refusal

def test_validate_agent_codex_rules() -> None:
    agent_run.validate_agent(OK)
    for model in ("gpt-5.5", "o3", "o4-mini", "codex-mini-latest", "openai/gpt-5.5"):
        agent_run.validate_agent({**OK, "model": model})
    with pytest.raises(ValueError, match="runs OpenAI models only"):
        agent_run.validate_agent({**OK, "model": "z-ai/glm-5.3"})
    with pytest.raises(ValueError, match="runs OpenAI models only"):
        agent_run.validate_agent({**OK, "models": ["anthropic/claude-sonnet-4.5"]})
    with pytest.raises(ValueError, match=r"accept_residual_tools: \["):
        agent_run.validate_agent({"framework": "codex", "model": MODEL})
    with pytest.raises(ValueError, match="decision D4"):
        agent_run.validate_agent({**OK, "accept_residual_tools": ["shell"]})
    with pytest.raises(ValueError, match="agent.accept_residual_tools must be a list"):
        agent_run.validate_agent({**OK, "accept_residual_tools": "apply_patch"})
    with pytest.raises(ValueError, match="agent.prices is required"):
        agent_run.validate_agent({**OK, "model": "gpt-5.5", "upstream_responses": "https://api.openai.com/v1"})
    agent_run.validate_agent({**OK, "upstream_responses": "https://api.openai.com/v1", "allow_unmetered": True})
    with pytest.raises(ValueError, match="agent.upstream_responses"):
        agent_run.validate_agent({**OK, "upstream_responses": "ftp://x"})
    agent_run.validate_agent({"framework": "goose", "accept_residual_tools": []})   # goose ignores the codex rule


def test_validate_agent_refuses_every_codex_command_override() -> None:
    for cmd in (list(CODEX.command_template()), ["codex", "exec", "hi"], ["sh", "-c", "codex"]):
        with pytest.raises(ValueError, match="agent.command overrides are not supported for framework codex"):
            agent_run.validate_agent({**OK, "command": cmd})
        assert CODEX.command_problem(cmd)
    assert CODEX.command_problem([]) == ""


def test_flags_sha256_differs_per_driver_and_per_codex_model() -> None:
    prints = {fw: agent_run.flags_sha256({"framework": fw}) for fw in ("goose", "claude", "copilot", "codex")}
    prints["copilot-anthropic"] = agent_run.flags_sha256({"framework": "copilot", "copilot_provider_type": "anthropic"})
    assert len(set(prints.values())) == len(prints)
    assert agent_run.flags_sha256({**OK, "model": "gpt-5.5"}) != agent_run.flags_sha256({**OK, "model": "o3"})
    assert agent_run.flags_sha256({**OK, "budget_usd": 7}) == agent_run.flags_sha256(OK)


# ---------------------------------------------------------------- self-test probe

def test_the_codex_probe_renders_with_the_operator_model() -> None:
    d = agent_drivers.driver_for(OK)
    assert d.probe_model == MODEL and d.dialect == "responses"
    src = agent_run.selftest_script(d)
    compile(src, "probe", "exec")
    assert "_cli.codex_checks(" in src and 'r["codex_home_fresh"]' in src and 'r["codex_state_writable"]' in src
    assert 'os.environ.get("OPENAI_API_KEY") == "injected-by-gateway"' in src
    assert MODEL in src and "/work/team/.codex/agents/vuln-remediation.toml" in src
    assert agent_drivers_codex.PROBE_MODEL in agent_run.selftest_script(CODEX)


FAKE_CODEX = textwrap.dedent(r'''
    import json, os, re, subprocess, sys, tomllib, urllib.request
    args, mode = sys.argv[1:], os.environ.get("FAKE_MODE", "good")
    cfg = {}
    for i, a in enumerate(args):
        if a == "-c":
            k, v = args[i + 1].split("=", 1)
            cfg[k] = tomllib.loads("v = " + v)["v"]
    base = cfg["model_providers.vulnscan_gateway"]["base_url"]
    instructions = open(cfg["model_instructions_file"]).read()
    tools = [{"type": "function", "name": "list_mcp_resources", "parameters": {}},
             {"type": "tool_search", "execution": "client", "description": "sources:\n- vulnscan"},
             {"type": "namespace", "name": "mcp__vulnscan", "tools": [
                 {"type": "function", "name": "get_summary", "parameters": {}}]},
             {"type": "custom", "name": "apply_patch", "format": {"type": "grammar"}}]
    inp = [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": instructions}]}]
    if mode == "shell":
        tools.append({"type": "function", "name": "shell", "parameters": {}})
    if mode == "codemode":
        inp.append({"type": "additional_tools", "tools": [{"type": "namespace", "name": "functions",
                                                            "tools": [{"type": "custom", "name": "exec"}]}]})
    if mode == "loads_project":
        inp.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": open("AGENTS.md").read()}]})
        srv = json.load(open(".mcp.json"))["mcpServers"]
        for s in srv.values():
            subprocess.run([s["command"], *s["args"]], timeout=10)
    if mode == "fail":
        print("Error: thread/start failed", file=sys.stderr); sys.exit(1)
    def post(items):
        body = {"model": args[args.index("-m") + 1], "stream": True, "store": False, "tools": tools,
                "input": inp + items}
        req = urllib.request.Request(base + "/responses", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=10).read().decode()
    reply = post([{"type": "message", "role": "user", "content": [{"type": "input_text", "text": args[-1]}]}])
    calls = [json.loads(l[5:])["item"] for l in reply.splitlines()
             if l.startswith("data:") and '"response.output_item.done"' in l]
    calls = [c for c in calls if c.get("type") == "custom_tool_call"]
    if calls:
        patch = calls[0]["input"]
        if mode == "writes":                             # a sandbox that does not block writes
            for path in re.findall(r"\*\*\* Add File: (.+)", patch):
                open(path, "w").write("canary")
            out = "Success."
        else:
            out = "patch rejected: writing is blocked by read-only sandbox; rejected by user approval settings"
        post([calls[0], {"type": "custom_tool_call_output", "call_id": calls[0]["call_id"], "output": out}])
    print(json.dumps({"type": "thread.started", "thread_id": "t"}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}))
''')


def _checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> tuple[dict, dict]:
    fake = tmp_path / "fake_codex.py"
    fake.write_text(FAKE_CODEX, encoding="utf-8")
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    argv = CODEX.command({"model": MODEL}, recipe="{recipe}", session="s", task="Reply ok.", run_id="t")
    for d in ("spool-requests", "codex-state"):   # stand-ins for /work/spool/requests and the state dir
        (tmp_path / d).mkdir(exist_ok=True)
    return selftest_cli.codex_checks(argv, recipe=str(tmp_path / "none.toml"), argv_url=agent_drivers_codex.GATEWAY_URL,
                                     binary=[sys.executable, str(fake)], timeout=60,
                                     write_targets=[str(tmp_path / "spool-requests"), str(tmp_path / "codex-state")])


def test_a_locked_down_codex_passes_every_probe_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "good")
    assert all(checks.values()) and set(checks) == {"codex_headless_ran", "codex_mcp_attached", "codex_tool_inventory",
                                                    "codex_canary_inert", "codex_residual_write_blocked"}, detail
    assert detail["residual_tools"] == ["apply_patch"] and detail["written"] == [] and detail["requests"] == 2
    assert "patch rejected" in detail["residual_outputs"][0] and detail["unexpected_tools"] == []
    assert "mcp__vulnscan/get_summary" in detail["offered_tools"]


def test_a_sandbox_that_lets_the_patch_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "writes")
    assert not checks["codex_residual_write_blocked"]
    assert detail["written"][:2] == ["cwd/rel-canary.txt", "out/abs.txt"]
    # rc.4 Low 3: the request spool and the state dir are targeted too; what the patch wrote there is removed again
    assert [Path(p).parent.name for p in detail["written"][2:]] == ["spool-requests", "codex-state"]
    assert all(not any((tmp_path / d).iterdir()) for d in ("spool-requests", "codex-state"))


@pytest.mark.parametrize(("mode", "unexpected"), [("shell", ["shell"]), ("codemode", ["functions/exec"])])
def test_another_tool_fails_the_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
                                          unexpected: list[str]) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, mode)
    assert not checks["codex_tool_inventory"] and detail["unexpected_tools"] == unexpected


def test_planted_files_that_take_effect_fail_the_canary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "loads_project")
    assert not checks["codex_canary_inert"] and detail["markers_fired"] == ["mcp-server"] and detail["canary_token_seen"]


def test_a_failing_or_missing_codex_does_not_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "fail")
    assert not checks["codex_headless_ran"] and not checks["codex_residual_write_blocked"] and detail["rc"] == 1
    checks, detail = selftest_cli.codex_checks(["/nonexistent/codex"], recipe="/none", argv_url="x", timeout=5)
    assert detail["rc"] == 127 and not any(v for k, v in checks.items() if k != "codex_canary_inert")


def test_codex_inventory_flattening_and_tool_search_attach() -> None:
    tools = [{"type": "namespace", "name": "mcp__vulnscan", "tools": [{"type": "function", "name": "escalate"}]},
             {"type": "tool_search", "execution": "server"}, {"type": "web_search"},
             {"type": "function", "function": {"name": "chat_style"}}]
    assert selftest_cli.tool_names(tools) == ["mcp__vulnscan/escalate", "type:tool_search:server", "type:web_search",
                                              "chat_style"]
    run = selftest_canary.StubRun(0, json.dumps({"type": "turn.completed"}), "", [{"type": "turn.completed"}],
                                  [{"path": "/v1/responses", "text": "- vulnscan",
                                    "body": {"tools": [{"type": "tool_search", "execution": "client"}]}}])
    checks, _ = selftest_cli.evaluate_codex(run, "TOKEN", [], [])
    assert checks["codex_mcp_attached"] and checks["codex_tool_inventory"] and checks["codex_residual_write_blocked"]


def test_the_stub_answers_responses() -> None:
    ctype, body = selftest_canary.canned_responses("m", True)
    assert ctype == "text/event-stream" and b"response.completed" in body and b'"output_text"' in body
    usage, found = gateway_dialects.usage_for("responses", body)
    assert found and usage.output_tokens == 1
    assert json.loads(selftest_canary.canned_responses("m", False)[1])["output"][0]["content"][0]["text"] == "ok"


def test_codex_home_fresh_refuses_anything_codex_would_read(tmp_path: Path) -> None:
    """The probe's ``codex_home_fresh`` (run in a subprocess with HOME = CODEX_HOME = a temp dir): Codex reads
    ``$CODEX_HOME/AGENTS.override.md`` even with ``project_doc_max_bytes=0`` (observed), so a run needs it absent."""
    entry = CODEX.selftest_entries()[0].split("from agentteams.vulnscan import selftest_cli")[0]
    helpers = ("import os, json\nr = {}\nhome = os.path.expanduser('~')\n"
               "def can_mkdir(d):\n    os.makedirs(d, exist_ok=True)\n    return True\n")
    for planted, fresh in ((None, True), ("AGENTS.override.md", False), ("skills", False), (".codex-state", True)):
        home = tmp_path / f"h-{planted}"
        home.mkdir()
        if planted:
            (home / planted).mkdir()
        src = helpers + entry.replace(agent_drivers_codex.CODEX_STATE, str(home / ".codex-state")) + "\nprint(json.dumps(r))"
        proc = subprocess.run([sys.executable, "-I", "-c", src], env={"HOME": str(home), "CODEX_HOME": str(home)},
                              capture_output=True, text=True, timeout=60, check=True)
        assert json.loads(proc.stdout)["codex_home_fresh"] is fresh, planted
