"""The GitHub Copilot CLI runtime driver (vulnscan-agent-runtimes plan, phase 4: BYOK, offline).

Docker-free and offline: env, argv and gateway wiring are pure functions; the self-test's headless run is
exercised against a fake ``copilot`` (a small Python script speaking the JSONL events the pinned release
emits) talking to the loopback stub, in both dialects (``openai`` chat completions, ``anthropic`` Messages).
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from agentteams.vulnscan import (
    agent_drivers,
    agent_drivers_copilot,
    agent_run,
    debrief,
    executor,
    mcp,
    selftest_cli,
)

RUN = Path("/state/2026-10-22")
SESSION = "vulnscan-2026-10-22"
COPILOT = agent_drivers.COPILOT
CHAT = agent_drivers.driver_for({"framework": "copilot"})
MESSAGES = agent_drivers.driver_for({"framework": "copilot", "copilot_provider_type": "anthropic"})
VULNSCAN_TOOLS = [f"vulnscan-{t}" for t in sorted(mcp.SCHEMAS)]


@pytest.fixture
def copilot_cfg(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(agent_run, "team_dir_problem", lambda *a, **k: None)
    monkeypatch.setattr(executor, "spool_dir", lambda: Path("/spool"))
    return {**agent_run.DEFAULT_AGENT, "framework": "copilot", "team_dir": "/team", "key_file": "/keys/openrouter.env",
            "model": "openai/gpt-5.5"}


def _flag(argv: list[str], name: str) -> str:
    """The value of ``--name=value`` or ``--name value``."""
    for i, part in enumerate(argv):
        if part.startswith(name + "="):
            return part.split("=", 1)[1]
        if part == name:
            return argv[i + 1]
    raise KeyError(name)


# ---------------------------------------------------------------- env, command, MCP wiring

def test_the_env_is_byok_offline_with_the_dummy_key() -> None:
    assert (CHAT.dialect, MESSAGES.dialect) == ("chat", "messages") and COPILOT.dialect == "chat"
    chat, messages = CHAT.env({}, "http://gw:8080"), MESSAGES.env({}, "http://gw:8080")
    assert chat["COPILOT_PROVIDER_BASE_URL"] == "http://gw:8080/api/v1" and chat["COPILOT_PROVIDER_TYPE"] == "openai"
    assert messages["COPILOT_PROVIDER_BASE_URL"] == "http://gw:8080" and messages["COPILOT_PROVIDER_TYPE"] == "anthropic"
    for env in (chat, messages):
        assert env["COPILOT_PROVIDER_API_KEY"] == agent_drivers.DUMMY_KEY and env["COPILOT_OFFLINE"] == "true"
        assert env["COPILOT_AUTO_UPDATE"] == "false" and env["COPILOT_MCP_TOOL_CACHE"] == "false"
        # rc.4 Medium 2: the home on the tmpfs (fresh), the code cache in the image, only logs on the host mount
        assert env["COPILOT_HOME"] == agent_drivers_copilot.COPILOT_HOME == agent_drivers.AGENT_HOME + "/.copilot"
        assert env["COPILOT_CACHE_HOME"] == agent_drivers_copilot.COPILOT_CACHE == "/opt/copilot-cache"
        assert COPILOT.state_dirs() == [agent_drivers_copilot.COPILOT_STATE]
        assert not any(v.startswith(agent_drivers_copilot.COPILOT_STATE) for v in env.values())
        assert not {"COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "COPILOT_ALLOW_ALL"} & set(env)
    assert COPILOT.run_env({"model": "m/x", "max_output_tokens": 20_000}) == {
        "COPILOT_MODEL": "m/x", "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS": "20000"}
    assert COPILOT.key_env == "COPILOT_PROVIDER_API_KEY" and COPILOT.model_env == "COPILOT_MODEL"


def test_the_command_locks_the_tools_down_and_loads_the_custom_agent() -> None:
    argv = COPILOT.command({}, recipe="/work/team/.github/agents/vuln-remediation.agent.md", session=SESSION,
                           task="TASK", run_id="R1")
    assert argv[:3] == ["copilot", "-p", "TASK"]
    assert _flag(argv, "--agent") == "vuln-remediation" and _flag(argv, "--add-dir") == "/work/team"
    assert _flag(argv, "--available-tools").split(",") == VULNSCAN_TOOLS
    assert set(_flag(argv, "--excluded-tools").split(",")) == set(agent_drivers_copilot.BUILTIN_TOOLS)
    assert {"bash", "view", "edit", "create", "apply_patch", "task", "skill", "web_fetch", "sql"} <= set(
        agent_drivers_copilot.BUILTIN_TOOLS)
    assert _flag(argv, "--allow-tool") == "vulnscan" and _flag(argv, "--output-format") == "json"
    for flag in ("--disable-builtin-mcps", "--no-ask-user", "--no-custom-instructions", "--no-auto-update",
                 "--disallow-temp-dir", "-s"):
        assert flag in argv, flag
    assert not {"--allow-all-tools", "--allow-all", "--yolo", "--allow-all-paths", "--attachment"} & set(argv)
    assert _flag(argv, "--log-dir") == agent_drivers_copilot.COPILOT_STATE + "/logs"   # the host mount: logs only
    cfg = json.loads(_flag(argv, "--additional-mcp-config"))
    assert list(cfg["mcpServers"]) == ["vulnscan"]
    server = cfg["mcpServers"]["vulnscan"]
    assert server["command"] == agent_drivers.PYTHON and server["args"] == ["-m", "agentteams.vulnscan.mcp"]
    assert server["env"] == agent_drivers.MCP_ENV and server["tools"] == ["*"] and server["type"] == "local"
    assert COPILOT.mcp_attach().kind == "copilot-additional-mcp-config"
    assert _flag(COPILOT.command_template(), "--add-dir") == "{team}"


def test_the_agent_definition_must_be_a_custom_agent_file() -> None:
    assert agent_drivers_copilot.agent_location("/t/.github/agents/x-y.agent.md") == ("/t", "x-y")
    assert agent_drivers_copilot.agent_location("/t/.github/agents/plain.md") == ("/t", "plain")
    for bad in ("/t/.goose/recipes/x.yaml", "/t/.github/agents/sub/x.agent.md", "/t/x.agent.md"):
        with pytest.raises(ValueError, match="custom agent file"):
            agent_drivers_copilot.agent_location(bad)
    agent_run.validate_agent({"framework": "copilot", "recipe": ".github/agents/other.agent.md"})
    with pytest.raises(ValueError, match="agent.recipe for framework copilot"):
        agent_run.validate_agent({"framework": "copilot", "recipe": ".goose/recipes/vuln-remediation.yaml"})


def test_agent_argv_for_copilot(copilot_cfg: dict) -> None:
    argv = agent_run.agent_argv(copilot_cfg, "img:p", RUN, SESSION, task="TASK TEXT", run_id="R1")
    image = argv.index("img:p")
    head, cmd = argv[:image], argv[image + 1:]
    assert f"/state/2026-10-22/agent/copilot-data:{agent_drivers_copilot.COPILOT_STATE}:rw" in head
    assert "COPILOT_MODEL=openai/gpt-5.5" in head and "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS=32000" in head
    assert "COPILOT_PROVIDER_API_KEY=injected-by-gateway" in head and "COPILOT_OFFLINE=true" in head
    assert _flag(cmd, "--add-dir") == "/work/team" and _flag(cmd, "--agent") == "vuln-remediation" and cmd[2] == "TASK TEXT"


def test_copilot_has_no_debrief(copilot_cfg: dict, tmp_path: Path) -> None:
    a = {**copilot_cfg, "team_dir": str(tmp_path)}
    assert COPILOT.debrief() is None and debrief._preflight({"agent": a}, a, tmp_path) == "no debrief for copilot"


# ---------------------------------------------------------------- outcome parsing (1.0.95 JSONL)

def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_parse_outcome() -> None:
    p = COPILOT.parse_outcome
    ok = p(_stream({"type": "model.call_finished", "data": {}}, {"type": "model.call_finished", "data": {}},
                   {"type": "result", "exitCode": 0}))
    assert ok == agent_drivers.Outcome("", 2)
    out = p(_stream({"type": "session.error", "data": {"message": "Output token limit exceeds the maximum"}},
                    {"type": "result", "exitCode": 1}))
    assert out.error == "copilot error: Output token limit exceeds the maximum" and out.model_calls == 0
    assert p(_stream({"type": "result", "exitCode": 1})).error == "copilot exited 1"
    assert p("No such agent: vuln-remediation, available: canary\n").error.startswith("No such agent")
    assert p("Error: unknown option\n").error == "Error: unknown option"
    assert p("").error == "copilot printed no result event" and p("").model_calls is None


# ---------------------------------------------------------------- gateway wiring and validation (D2)

def test_gateway_argv_per_provider_type(copilot_cfg: dict) -> None:
    chat = agent_run.gateway_argv(copilot_cfg, "img:p", Path("/spend"))
    assert "/keys/openrouter.env:/run/secrets/openrouter.env:ro" in chat and "--dialects" not in chat
    assert _flag(chat, "--upstream") == "http://host.docker.internal:8791" and _flag(chat, "--model") == "openai/gpt-5.5"
    a = {**copilot_cfg, "copilot_provider_type": "anthropic", "model": "anthropic/claude-sonnet-4.5"}
    messages = agent_run.gateway_argv(a, "img:p", Path("/spend"))
    assert _flag(messages, "--dialects") == "messages" and _flag(messages, "--upstream-messages") == "https://openrouter.ai/api/v1"
    assert "/keys/openrouter.env:/run/secrets/messages.env:ro" in messages


def test_validate_agent_copilot_rules() -> None:
    agent_run.validate_agent({"framework": "copilot"})                       # chat: any OpenRouter model
    agent_run.validate_agent({"framework": "copilot", "copilot_provider_type": "anthropic",
                              "model": "anthropic/claude-sonnet-4.5"})
    with pytest.raises(ValueError, match="runs Anthropic models only"):
        agent_run.validate_agent({"framework": "copilot", "copilot_provider_type": "anthropic"})
    with pytest.raises(ValueError, match="agent.copilot_provider_type must be one of openai, anthropic"):
        agent_run.validate_agent({"framework": "copilot", "copilot_provider_type": "azure"})
    for cmd in (list(COPILOT.command_template()), ["copilot", "-p", "x", "--allow-all"]):
        with pytest.raises(ValueError, match="agent.command overrides are not supported for framework copilot"):
            agent_run.validate_agent({"framework": "copilot", "command": cmd})


# ---------------------------------------------------------------- self-test probe

def test_the_copilot_probe_renders_for_each_dialect() -> None:
    for d in (CHAT, MESSAGES):
        src = agent_run.selftest_script(d)
        compile(src, "probe", "exec")
        assert "_cli.copilot_checks(" in src and 'r["copilot_home_fresh"]' in src and 'r["copilot_state_writable"]' in src
        assert 'os.environ.get("COPILOT_PROVIDER_API_KEY") == "injected-by-gateway"' in src
        assert repr(d.dialect) in src and "/work/team/.github/agents/vuln-remediation.agent.md" in src
        assert "--add-dir={team}" in src


FAKE_COPILOT = textwrap.dedent(r'''
    import json, os, subprocess, sys, urllib.request
    args, mode = sys.argv[1:], os.environ.get("FAKE_MODE", "good")
    def flag(n):
        return next(a.split("=", 1)[1] for a in args if a.startswith(n + "="))
    def emit(kind, **data):
        print(json.dumps({"type": kind, "data": data}))
    try:
        agent = open(os.path.join(flag("--add-dir"), ".github", "agents", flag("--agent") + ".agent.md")).read()
    except OSError:
        print("No such agent: " + flag("--agent"), file=sys.stderr); sys.exit(1)
    tools = flag("--available-tools").split(",")
    system = agent
    if mode == "extra":
        tools.append("bash")
    if mode == "loads_project":                       # what a CLI with workspace trust would do
        system += open("AGENTS.md").read()
        for s in json.load(open(".mcp.json"))["mcpServers"].values():
            subprocess.run([s["command"], *s["args"]], timeout=10)
    emit("session.info", infoType="configuration", message="Disabled tools: bash, view")
    if mode == "fail":
        emit("session.error", errorType="query", message="boom")
        print(json.dumps({"type": "result", "exitCode": 1})); sys.exit(1)
    base, kind = os.environ["COPILOT_PROVIDER_BASE_URL"], os.environ["COPILOT_PROVIDER_TYPE"]
    model = os.environ["COPILOT_MODEL"]
    if kind == "anthropic":
        url = base + "/v1/messages"
        body = {"model": model, "max_tokens": 10, "stream": True, "system": system,
                "tools": [{"name": t, "input_schema": {}} for t in tools], "messages": [{"role": "user", "content": args[1]}]}
    else:
        url = base + "/chat/completions"
        body = {"model": model, "max_tokens": 10, "stream": True, "messages": [{"role": "system", "content": system}],
                "tools": [{"type": "function", "function": {"name": t, "parameters": {}}} for t in tools]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()
    emit("model.call_finished", outcome="success")
    print(json.dumps({"type": "result", "exitCode": 0}))
''')


def _checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, d: agent_drivers.Driver = CHAT) -> tuple[dict, dict]:
    fake = tmp_path / "fake_copilot.py"
    fake.write_text(FAKE_COPILOT, encoding="utf-8")
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    for k, v in d.env({}, "http://unused").items():
        monkeypatch.setenv(k, v)
    probe = agent_drivers_copilot.PROBE
    argv = d.command(probe, recipe="{team}/" + d.default_recipe, session="s", task="Reply ok.", run_id="t")
    agent = tmp_path / "vuln-remediation.agent.md"
    agent.write_text("---\nname: vuln-remediation\ndescription: \"vulnscan remediation\"\n---\nYou triage.\n",
                     encoding="utf-8")
    return selftest_cli.copilot_checks(argv, d.run_env(probe), recipe=str(agent), dialect=d.dialect,
                                       binary=[sys.executable, str(fake)], timeout=60)


@pytest.mark.parametrize("d", [CHAT, MESSAGES], ids=["chat", "messages"])
def test_a_locked_down_copilot_passes_every_probe_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         d: agent_drivers.Driver) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "good", d)
    assert checks == {"copilot_agent_file_clean": True, "copilot_headless_ran": True, "copilot_mcp_attached": True,
                      "copilot_tool_inventory": True, "copilot_canary_inert": True}, detail
    assert detail["offered_tools"] == VULNSCAN_TOOLS and detail["markers_fired"] == []
    assert detail["request_paths"] == (["/api/v1/chat/completions"] if d is CHAT else ["/v1/messages"])
    assert detail["cli_info"] == ["Disabled tools: bash, view"]


def test_a_built_in_tool_fails_the_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "extra")
    assert not checks["copilot_tool_inventory"] and detail["unexpected_tools"] == ["bash"]


def test_planted_files_that_take_effect_fail_the_canary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "loads_project")
    assert not checks["copilot_canary_inert"] and detail["markers_fired"] == ["mcp-server"] and detail["canary_token_seen"]


def test_a_failing_or_missing_copilot_does_not_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, detail = _checks(tmp_path, monkeypatch, "fail")
    assert not checks["copilot_headless_ran"] and detail["errors"] == ["boom"] and detail["rc"] == 1
    agent = tmp_path / "vuln-remediation.agent.md"   # written by _checks above
    checks, detail = selftest_cli.copilot_checks(["/nonexistent/copilot"], {}, recipe=str(agent), dialect="chat", timeout=5)
    assert detail["rc"] == 127 and not any(v for k, v in checks.items() if k not in ("copilot_canary_inert",
                                                                                      "copilot_agent_file_clean"))


# ---------------------------------------------------------------- image (revision 12)

def test_the_image_builds_copilot_from_the_committed_lockfile() -> None:
    text = (agent_run.dockerfile_dir() / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM ${NODE_IMAGE} AS copilot-build" in text and "npm ci --ignore-scripts" in text
    assert "npm audit signatures" in text and "COPY copilot/package.json copilot/package-lock.json ./" in text
    assert "LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum" in text
    assert 'if [ "$TREE" != "$CLI_SHA256" ]' in text
    assert "COPY --from=copilot-build /opt/copilot/bin/copilot /usr/local/bin/copilot" in text
    lock = json.loads((agent_run.dockerfile_dir() / "copilot" / "package-lock.json").read_text(encoding="utf-8"))
    pkgs = lock["packages"]
    assert agent_drivers_copilot.lockfile_version() == COPILOT.image.version
    for arch in ("linux-x64", "linux-arm64"):
        entry = pkgs["node_modules/@github/copilot-" + arch]
        assert entry["version"] == COPILOT.image.version and entry["integrity"].startswith("sha512-")
    assert all(not e.get("hasInstallScript") for e in pkgs.values())
    assert dict(COPILOT.image.sha256).keys() == {"amd64", "arm64"}
    node = dict(COPILOT.image.build_args)["NODE_IMAGE"]
    assert "@sha256:" in node and len(node.split("@sha256:")[1]) == 64
    argv = agent_run.build_argv(Path("/ctx"), "img", COPILOT.image.version, "ab" * 32, "py@sha256:1", framework="copilot")
    assert argv[argv.index(f"NODE_IMAGE={node}") - 1] == "--build-arg" and "AGENT_CLI=copilot" in argv
