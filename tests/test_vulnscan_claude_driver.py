"""The Claude Code runtime driver (vulnscan-agent-runtimes plan, phase 3).

Docker-free and offline: argv, env and gateway wiring are pure functions; the self-test's headless run is
exercised against a fake ``claude`` (a small Python script) talking to the loopback stub, so the tool
inventory and canary judgements are tested without the real CLI or any network.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from agentteams.vulnscan import (
    agent_drivers,
    agent_run,
    agent_upstreams,
    debrief,
    executor,
    selftest_canary,
)

RUN = Path("/state/2026-10-22")
SESSION = "vulnscan-2026-10-22"
CLAUDE_MODEL = "anthropic/claude-sonnet-4.5"


@pytest.fixture
def claude_cfg(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(agent_run, "team_dir_problem", lambda *a, **k: None)
    monkeypatch.setattr(executor, "spool_dir", lambda: Path("/spool"))
    return {**agent_run.DEFAULT_AGENT, "framework": "claude", "team_dir": "/team", "key_file": "/keys/openrouter.env",
            "model": CLAUDE_MODEL}


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


# ---------------------------------------------------------------- env, command, MCP wiring

def test_the_container_env_holds_only_the_dummy_key_and_turns_side_channels_off() -> None:
    env = agent_drivers.CLAUDE.env({}, "http://gw:8080")
    assert env["ANTHROPIC_BASE_URL"] == "http://gw:8080" and env["ANTHROPIC_API_KEY"] == agent_drivers.DUMMY_KEY
    assert env["CLAUDE_CONFIG_DIR"] == agent_drivers.CLAUDE_STATE == agent_drivers.CLAUDE.state_dirs()[0]
    assert env["CLAUDE_CONFIG_DIR"].startswith(agent_drivers.AGENT_HOME + "/")
    for var in ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_AUTOUPDATER", "DISABLE_UPDATES",
                "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "CLAUDE_CODE_DISABLE_AUTO_MEMORY"):
        assert env[var] == "1", var
    assert env["ENABLE_TOOL_SEARCH"] == "false"
    assert not {"ANTHROPIC_AUTH_TOKEN", "OPENROUTER_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"} & set(env)


def test_every_model_alias_is_pinned_and_the_output_setting_equals_the_gateway_ceiling() -> None:
    run_env = agent_drivers.CLAUDE.run_env({"model": CLAUDE_MODEL, "max_output_tokens": 20_000})
    pinned = {k: v for k, v in run_env.items() if k != "CLAUDE_CODE_MAX_OUTPUT_TOKENS"}
    assert set(pinned) == {"ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
                           "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"}
    assert set(pinned.values()) == {CLAUDE_MODEL}
    assert run_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "20000"
    gw = agent_run.gateway_argv({**agent_run.DEFAULT_AGENT, "framework": "claude", "model": CLAUDE_MODEL,
                                 "key_file": "/k", "max_output_tokens": 20_000}, "img", Path("/spend"))
    assert _flag(gw, "--max-output-tokens") == "20000"


def test_the_command_locks_the_tools_down_and_passes_the_agent_definition() -> None:
    argv = agent_drivers.CLAUDE.command({"model": CLAUDE_MODEL, "max_turns": 40}, recipe="/work/team/a.md",
                                        session=SESSION, task="TASK", run_id="R1")
    assert argv[:4] == ["claude", "--bare", "-p", "TASK"]             # the prompt follows -p directly
    assert _flag(argv, "--tools") == "" and "--strict-mcp-config" in argv and "--disable-slash-commands" in argv
    assert _flag(argv, "--append-system-prompt-file") == "/work/team/a.md"
    assert _flag(argv, "--allowedTools") == "mcp__vulnscan__*" and _flag(argv, "--permission-mode") == "dontAsk"
    assert _flag(argv, "--output-format") == "stream-json" and "--verbose" in argv
    assert _flag(argv, "--max-turns") == "40" and _flag(argv, "--model") == CLAUDE_MODEL
    assert _flag(argv, "--name") == SESSION
    for variadic in ("--tools", "--mcp-config", "--allowedTools"):      # never followed by a positional
        assert argv[argv.index(variadic) + 2].startswith("--"), variadic
    cfg = json.loads(_flag(argv, "--mcp-config"))
    assert list(cfg["mcpServers"]) == ["vulnscan"]
    server = cfg["mcpServers"]["vulnscan"]
    assert server["type"] == "stdio" and server["command"] == "/usr/local/bin/python"
    assert server["args"] == ["-m", "agentteams.vulnscan.mcp"]            # the module goose's extension runs
    assert agent_drivers.MCP_COMMAND.split()[1:] == server["args"]
    assert agent_drivers.CLAUDE.mcp_attach().kind == "claude-mcp-config"


def test_the_mcp_env_matches_the_container_env() -> None:
    base = agent_run._base_argv(RUN, Path("/s"), Path("/team"), Path("/spool"), name="x", driver=agent_drivers.CLAUDE)
    env = dict(base[i + 1].split("=", 1) for i, a in enumerate(base) if a == "-e")
    assert {k: env[k] for k in agent_drivers.MCP_ENV} == agent_drivers.MCP_ENV


def test_agent_argv_for_claude(claude_cfg: dict) -> None:
    argv = agent_run.agent_argv(claude_cfg, "img:c", RUN, SESSION, task="TASK TEXT", run_id="R1")
    image = argv.index("img:c")
    head, cmd = argv[:image], argv[image + 1:]
    assert ["-w", "/work/cwd"] == head[head.index("-w"):head.index("-w") + 2]
    assert f"/state/2026-10-22/agent/claude-data:{agent_drivers.CLAUDE_STATE}:rw" in head
    assert f"ANTHROPIC_DEFAULT_HAIKU_MODEL={CLAUDE_MODEL}" in head and "ANTHROPIC_API_KEY=injected-by-gateway" in head
    assert _flag(cmd, "--append-system-prompt-file") == "/work/team/.claude/agents/vuln-remediation.md"
    assert cmd[3] == "TASK TEXT" and _flag(cmd, "--max-turns") == "100"
    assert not any("sk-" in part for part in argv)


def test_debrief_resumes_the_named_session_with_the_same_lockdown(claude_cfg: dict) -> None:
    argv = debrief.debrief_argv(claude_cfg, "img:c", RUN, SESSION)
    cmd = argv[argv.index("img:c") + 1:]
    assert cmd[:4] == ["claude", "--bare", "--resume", SESSION]
    assert _flag(cmd, "--tools") == "" and "--strict-mcp-config" in cmd and _flag(cmd, "--permission-mode") == "dontAsk"
    assert "/spool/requests:/work/spool/requests:ro" in argv


# ---------------------------------------------------------------- outcome parsing

def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_parse_outcome_success_counts_model_calls() -> None:
    t = _stream({"type": "system", "subtype": "init", "tools": []},
                {"type": "assistant", "message": {"id": "m1"}}, {"type": "assistant", "message": {"id": "m1"}},
                {"type": "assistant", "message": {"id": "m2"}},
                {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2})
    assert agent_drivers.CLAUDE.parse_outcome(t) == agent_drivers.Outcome("", 2)


def test_parse_outcome_errors() -> None:
    p = agent_drivers.CLAUDE.parse_outcome
    out = p(_stream({"type": "assistant", "message": {"id": "m1"}},
                    {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""}))
    assert out.error.startswith("claude error_max_turns") and out.model_calls == 1
    out = p(_stream({"type": "result", "subtype": "success", "is_error": True, "result": "API Error: 400 refused"}))
    assert "API Error: 400 refused" in out.error and out.model_calls == 0
    out = p(_stream({"type": "system", "subtype": "api_retry", "error": "invalid_request", "error_status": 400}))
    assert out.error == "claude API error invalid_request (HTTP 400)" and out.model_calls is None
    assert p("Error: unknown option --bogus\n").error == "Error: unknown option --bogus"
    assert p("").error == "claude printed no result event"
    assert len(p(_stream({"type": "result", "subtype": "x", "is_error": True, "result": "y" * 900})).error) == 300


# ---------------------------------------------------------------- gateway wiring

def test_gateway_argv_for_claude_via_openrouter(claude_cfg: dict) -> None:
    argv = agent_run.gateway_argv(claude_cfg, "img:c", Path("/spend"))
    assert "/keys/openrouter.env:/run/secrets/messages.env:ro" in argv
    assert not any(a.endswith(":/run/secrets/openrouter.env:ro") for a in argv)   # no chat route, no chat key
    assert _flag(argv, "--dialects") == "messages" and _flag(argv, "--model") == CLAUDE_MODEL
    assert _flag(argv, "--upstream-messages") == "https://openrouter.ai/api/v1"
    assert _flag(argv, "--key-file-messages") == "/run/secrets/messages.env"
    assert _flag(argv, "--anthropic-betas") == ",".join(agent_upstreams.DEFAULT_ANTHROPIC_BETAS)
    assert _flag(argv, "--max-output-tokens") == "32000" and "--prices" not in argv and "--models" not in argv
    assert _flag(argv, "--max-tokens-total") == "10000000" and _flag(argv, "--budget-usd") == "2.0"
    assert "--upstream" not in argv and "--key-file" not in argv


def test_gateway_argv_for_claude_via_anthropic(claude_cfg: dict, monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    a = {**claude_cfg, "upstream_messages": "https://api.anthropic.com/v1", "prices": "/cfg/prices.json",
         "models": [CLAUDE_MODEL, "claude-helper-x"], "anthropic_betas": []}
    argv = agent_run.gateway_argv(a, "img:c", Path("/spend"))
    assert f"{tmp_path}/.config/agentteams-secrets/anthropic.env:/run/secrets/messages.env:ro" in argv
    assert "/cfg/prices.json:/run/secrets/prices.json:ro" in argv and _flag(argv, "--prices") == "/run/secrets/prices.json"
    assert _flag(argv, "--models") == "claude-helper-x" and _flag(argv, "--anthropic-betas") == ""
    explicit = agent_run.gateway_argv({**a, "key_file_messages": "/k/m.env"}, "img:c", Path("/spend"))
    assert "/k/m.env:/run/secrets/messages.env:ro" in explicit
    assert agent_upstreams.secret_paths({**a, "key_file_messages": "/k/m.env"}) == (
        "/keys/openrouter.env", "/k/m.env", "/cfg/prices.json")


def test_flags_sha256_differs_between_goose_and_claude() -> None:
    goose, claude = agent_run.flags_sha256({"framework": "goose"}), agent_run.flags_sha256({"framework": "claude"})
    assert goose != claude
    assert claude == agent_run.flags_sha256({"framework": "claude", "model": CLAUDE_MODEL, "budget_usd": 5})


# ---------------------------------------------------------------- validation (revision 13, D3)

def test_validate_agent_claude_rules() -> None:
    ok = {"framework": "claude", "model": CLAUDE_MODEL}
    agent_run.validate_agent(ok)
    agent_run.validate_agent({**ok, "model": "claude-sonnet-4-5", "models": ["claude-haiku-4-5"]})
    with pytest.raises(ValueError, match="runs Anthropic models only"):
        agent_run.validate_agent({"framework": "claude"})               # the default GLM model
    with pytest.raises(ValueError, match="runs Anthropic models only"):
        agent_run.validate_agent({**ok, "models": ["z-ai/glm-5.3"]})
    anthropic = {**ok, "model": "claude-sonnet-4-5", "upstream_messages": "https://api.anthropic.com/v1"}
    with pytest.raises(ValueError, match="agent.prices is required"):
        agent_run.validate_agent(anthropic)
    agent_run.validate_agent({**anthropic, "prices": "/p.json"})
    agent_run.validate_agent({**anthropic, "allow_unmetered": True})
    for key, bad in (("upstream_messages", "ftp://x"), ("models", "claude-a"), ("models", ["a,b"]),
                     ("anthropic_betas", ["bad beta"]), ("max_output_tokens", 0), ("max_output_tokens", 10 ** 6),
                     ("max_turns", True), ("prices", 3), ("key_file_messages", None)):
        with pytest.raises(ValueError, match=f"agent.{key}"):
            agent_run.validate_agent({**ok, key: bad})
    agent_run.validate_agent({"framework": "goose", "max_turns": 5})    # goose ignores the claude-only rule


# ---------------------------------------------------------------- self-test probe and canary

def test_the_claude_probe_renders_and_binds_the_lockdown() -> None:
    src = agent_run.selftest_script(agent_drivers.CLAUDE)
    compile(src, "probe", "exec")
    assert 'os.environ.get("ANTHROPIC_API_KEY") == "injected-by-gateway"' in src
    assert "import selftest_canary as _canary" in src and "_canary.claude_checks(" in src and 'r["provider_apis_blocked"]' in src
    assert "https://api.anthropic.com/v1/models" in src and "SELFTEST-DETAIL" in src
    assert "/work/team/.claude/agents/vuln-remediation.md" in src


FAKE_CLAUDE = textwrap.dedent(r'''
    import json, os, subprocess, sys, urllib.request
    args, mode = sys.argv[1:], os.environ.get("FAKE_MODE", "good")
    def flag(n):
        return args[args.index(n) + 1]
    tools = ["mcp__vulnscan__get_summary", "mcp__vulnscan__submit_report", "EndConversation"]
    servers = [{"name": "vulnscan", "status": "connected"}]
    system = open(flag("--append-system-prompt-file")).read()
    if mode == "bash":
        tools.append("Bash")
    if mode == "loads_project":                       # what a CLI without --bare would do
        system += open("CLAUDE.md").read()
        cfg = json.load(open(".mcp.json"))["mcpServers"]
        for name, s in cfg.items():
            subprocess.run([s["command"], *s["args"]], timeout=10)
            servers.append({"name": name, "status": "connected"})
    if mode == "request_tool":                        # the init event is clean but a request carries a tool
        extra = [{"name": "WebFetch", "input_schema": {}}]
    else:
        extra = []
    if mode == "fail":
        print("Error: something broke", file=sys.stderr); sys.exit(1)
    print(json.dumps({"type": "system", "subtype": "init", "tools": tools, "mcp_servers": servers}))
    body = {"model": flag("--model"), "max_tokens": 10, "stream": True, "system": system,
            "tools": [{"name": t, "input_schema": {}} for t in tools] + extra,
            "messages": [{"role": "user", "content": args[2]}]}
    req = urllib.request.Request(os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages?beta=true",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    reply = urllib.request.urlopen(req, timeout=10).read().decode()
    assert "message_stop" in reply
    print(json.dumps({"type": "assistant", "message": {"id": "msg_selftest"}}))
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1}))
''')


def _checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> tuple[dict, dict]:
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE, encoding="utf-8")
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    probe = {"model": agent_drivers.SELFTEST_MODEL, "max_turns": 2, "max_output_tokens": 1024}
    argv = agent_drivers.CLAUDE.command(probe, recipe="{recipe}", session="s", task="Reply ok.", run_id="t")
    return selftest_canary.claude_checks(argv, agent_drivers.CLAUDE.run_env(probe), recipe=str(tmp_path / "none.md"),
                                         binary=[sys.executable, str(fake)], timeout=60)


def test_a_locked_down_cli_passes_every_probe_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "good")
    assert checks == {"claude_headless_ran": True, "claude_mcp_attached": True, "claude_tool_inventory": True,
                      "claude_canary_inert": True}, detail
    assert detail["unexpected_tools"] == [] and detail["requests"] == 1 and detail["markers_fired"] == []
    assert "mcp__vulnscan__get_summary" in detail["init_tools"]


def test_a_built_in_tool_fails_the_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "bash")
    assert not checks["claude_tool_inventory"] and detail["unexpected_tools"] == ["Bash"]
    checks, detail = _checks(tmp_path, monkeypatch, "request_tool")
    assert not checks["claude_tool_inventory"] and detail["unexpected_tools"] == ["WebFetch"]


def test_planted_files_that_take_effect_fail_the_canary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "loads_project")
    assert not checks["claude_canary_inert"] and not checks["claude_mcp_attached"]
    assert detail["markers_fired"] == ["mcp-server"] and detail["canary_token_seen"]


def test_a_cli_that_fails_does_not_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "fail")
    assert not checks["claude_headless_ran"] and not checks["claude_tool_inventory"] and detail["rc"] == 1
    assert "something broke" in detail["stderr_tail"]


def test_a_missing_cli_does_not_pass(tmp_path: Path) -> None:
    checks, detail = selftest_canary.claude_checks(["/nonexistent/claude"], {}, recipe="/none", timeout=5)
    assert not checks["claude_headless_ran"] and not checks["claude_tool_inventory"]
    assert not checks["claude_mcp_attached"] and detail["rc"] == 127


def test_the_stub_answers_both_dialects() -> None:
    ctype, body = selftest_canary.canned_messages("m", True)
    assert ctype == "text/event-stream" and body.count(b"event: ") == 6 and b"message_stop" in body
    ctype, body = selftest_canary.canned_chat("m", False)
    assert json.loads(body)["choices"][0]["message"]["content"] == "ok"
    assert selftest_canary.vulnscan_tools() >= {"mcp__vulnscan__request", "mcp__vulnscan__escalate"}


# ---------------------------------------------------------------- agent.command override (security review, item 1)

def _override(extra: list[str] | None = None) -> list[str]:
    """A claude override that keeps the lockdown (the driver's template, model fixed) plus ``extra``."""
    cmd = [CLAUDE_MODEL if part == "{model}" else part for part in agent_drivers.CLAUDE.command_template()]
    return [*cmd, *(extra or [])]


def test_the_selftest_probe_runs_the_command_override(claude_cfg: dict) -> None:
    override = _override(["--fallback-model", "override-marker"])
    src = agent_run.selftest_script(agent_drivers.CLAUDE, override)
    probe_argv = [p.replace("{session}", "vulnscan-selftest").replace("{task}", "Reply with the single word ok.")
                  .replace("{run_id}", "selftest") for p in override]
    assert repr(json.dumps(probe_argv)) in src                        # exactly the override, {recipe} for the probe
    assert src != agent_run.selftest_script(agent_drivers.CLAUDE)
    assert agent_run.selftest_script(agent_drivers.CLAUDE, []) == agent_run.selftest_script(agent_drivers.CLAUDE)
    cfg = {**claude_cfg, "command": override}
    assert agent_run.selftest_argv(cfg, "img", RUN)[-1] == src         # the self-test container runs it
    assert "override-marker" in agent_run.agent_argv(cfg, "img", RUN, SESSION)   # and so does a run
    assert agent_run.flags_sha256(cfg) != agent_run.flags_sha256(claude_cfg)
    # goose's probe runs no CLI command: an override leaves it (and goose's golden probe) unchanged
    assert agent_run.selftest_script(agent_drivers.GOOSE, ["goose", "run"]) == agent_run.selftest_script()


def test_validate_agent_refuses_every_claude_command_override() -> None:
    """claude's lockdown is part of the driver: no override is accepted, not even the driver's own template
    (a wrapper program or a second --mcp-config would otherwise slip past a flag check; @security rc.3)."""
    ok = {"framework": "claude", "model": CLAUDE_MODEL}
    agent_run.validate_agent(ok)                                       # no override: fine
    assert agent_drivers.CLAUDE.command_problem([]) == ""

    def without(flag: str, n: int = 1) -> list[str]:
        cmd = _override()
        i = cmd.index(flag)
        return cmd[:i] + cmd[i + n:]

    def replaced(flag: str, value: str) -> list[str]:
        cmd = _override()
        cmd[cmd.index(flag) + 1] = value
        return cmd

    def widened(flag: str, value: str) -> list[str]:
        cmd = _override()
        i = cmd.index(flag) + 2
        return cmd[:i] + [value] + cmd[i:]

    bad = {
        "no --bare": without("--bare"),
        "no --strict-mcp-config": without("--strict-mcp-config"),
        "no --tools": without("--tools", 2),
        "no --allowedTools": without("--allowedTools", 2),
        "no --permission-mode": without("--permission-mode", 2),
        "tools Bash": replaced("--tools", "Bash"),
        "tools widened": widened("--tools", "Bash"),
        "allowed widened": widened("--allowedTools", "Bash"),
        "allowed all": replaced("--allowedTools", "*"),
        "bypass": replaced("--permission-mode", "bypassPermissions"),
        "tools twice": _override(["--tools", "Bash"]),
        "skip permissions": _override(["--dangerously-skip-permissions"]),
        "alias": _override(["--allowed-tools", "Bash"]),
        "equals form": _override(["--tools=Bash"]),
        "settings": _override(["--settings", "/work/team/settings.json"]),
        "the driver's own template": list(agent_drivers.CLAUDE.command_template()),
        "harmless extra flag": _override(["--fallback-model", CLAUDE_MODEL]),
        "wrapper program": ["sh", "-c", "claude", *_override()[1:]],
        "second mcp config": _override(["--mcp-config", '{"mcpServers":{"vulnscan":{"command":"sh"}}}']),
    }
    for name, cmd in bad.items():
        with pytest.raises(ValueError, match="agent.command overrides are not supported for framework claude"):
            agent_run.validate_agent({**ok, "command": cmd})
        assert agent_drivers.CLAUDE.command_problem(cmd), name
    agent_run.validate_agent({"framework": "goose", "command": ["goose", "run", "--recipe", "{recipe}"]})
