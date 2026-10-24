"""The Codex CLI runtime driver (vulnscan-agent-runtimes plan, phase 4; split from :mod:`.agent_drivers`).

``codex exec`` over the gateway's OpenAI Responses route, the vulnscan MCP server and every setting passed as
``-c`` overrides, so the whole configuration is part of the command the flags fingerprint hashes and no
config file is written before the run. Observed with the pinned release (0.162.1, its macOS build against a
loopback stub, 2026-10-24) and its ``config-schema.json`` release asset:

- ``--ignore-user-config`` skips ``$CODEX_HOME/config.toml``; ``--ignore-rules`` skips user and project
  ``.rules``; ``--strict-config`` makes an unknown key an error, so a renamed setting fails the self-test
  instead of being ignored; ``--ephemeral`` writes no session files (there is no debrief);
- ``project_doc_max_bytes=0`` loads no project ``AGENTS.md``; the working directory is the empty tmpfs anyway.
  ``$CODEX_HOME/AGENTS.override.md`` (global instructions) is still read (observed: the canary caught it), so
  ``CODEX_HOME`` is the tmpfs home itself, empty at every start (``codex_home_fresh``; Codex refuses a
  ``CODEX_HOME`` that does not exist yet); only the logs and the sqlite state DB go to the host-backed state dir
  (``log_dir``, ``sqlite_home``);
- the custom provider ``vulnscan_gateway`` (``wire_api="responses"``, ``env_key="OPENAI_API_KEY"`` = the dummy
  key, no websockets, no standalone web search, local compaction) is the only model route;
- ``--disable`` turns off every stable feature that adds a tool or a side channel (shell, unified exec, multi
  agent, apps, plugins, browser, computer use, image tools, hooks, memories, code-mode host, model discovery,
  request compression ...); ``web_search="disabled"``, ``tools.update_plan`` and request-user-input off;
- ``-s read-only`` and ``approval_policy="never"``: a tool that would write is rejected, never asked about.

What remains is model-dependent (the CLI picks tools from the model's catalog entry): the vulnscan tools
(``mcp__vulnscan`` namespace, or deferred behind a client-side ``tool_search``), the read-only MCP resource
tools (only the vulnscan server is attached, and it serves no resources) and, for models whose catalog entry
names one, **``apply_patch``** (no switch turns it off: residual tool, decision D4). Codex is therefore
accepted only with ``agent.accept_residual_tools: ["apply_patch"]`` (:meth:`CodexDriver.settings_problem`) and
a self-test that shows a patch is rejected by the read-only sandbox (``codex_residual_write_blocked``,
:func:`agentteams.vulnscan.selftest_cli.codex_checks`). The self-test probes with ``agent.model`` (the stub is
not billed), because the tool set follows the model: changing the model asks for a new self-test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentteams.vulnscan.agent_driver_base import (
    AGENT_HOME,
    DUMMY_KEY,
    MCP_COMMAND,
    MCP_ENV,
    PYTHON,
    TEAM_IN_CONTAINER,
    DebriefSupport,
    Driver,
    ImageRecipe,
    McpAttach,
    Outcome,
    json_or_none,
)

#: ``CODEX_HOME``: the tmpfs home itself, so it is empty at every start (Codex reads global instructions, skills
#: and rules from it, and refuses one that does not exist). ``--ignore-user-config`` skips a ``config.toml`` anyway.
CODEX_HOME = AGENT_HOME
#: What Codex would read from ``CODEX_HOME``: none of it may exist when a run starts (``codex_home_fresh``).
CODEX_HOME_READS = ("AGENTS.md", "AGENTS.override.md", "config.toml", "hooks.json", "skills", "rules", "plugins",
                    "prompts", ".agents")
#: Codex's logs and sqlite state DB (no sessions: ``--ephemeral``), the one host-backed writable mount
#: (``<run>/agent/codex-data``); nothing in it is read as configuration.
CODEX_STATE = f"{AGENT_HOME}/.codex-state"
#: The run's gateway, as :mod:`.agent_run` names it (``http://<GATEWAY>:<GW_PORT>``): Codex takes its provider
#: ``base_url`` from config only, so the URL is part of the command (the self-test swaps in its stub).
GATEWAY_URL = "http://agentteams-vulnscan-gw:8080"
PROVIDER = "vulnscan_gateway"
#: Residual built-in tools a model may still be offered, and the setting that accepts them (D4).
RESIDUAL_TOOLS = ("apply_patch",)
#: The model the self-test probes with when ``agent.model`` is not known (the flags fingerprint's default).
PROBE_MODEL = "gpt-5.5"
#: Stable features of the pinned release (``codex features list``) that add a tool or a side channel.
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "unified_exec_tty", "shell_snapshot", "multi_agent", "apps", "plugins",
    "remote_plugin", "plugin_sharing", "browser_use", "browser_use_external", "browser_use_full_cdp_access",
    "browser_annotation_api", "in_app_browser", "computer_use", "image_generation", "view_image", "goals",
    "guardian_approval", "hooks", "code_mode_host", "skill_search", "skill_mcp_dependency_install", "sleep_tool",
    "tool_suggest", "tool_call_mcp_elicitation", "realtime_conversation", "workspace_dependencies", "worktrees",
    "write_stdin_approval", "daemon_auto_start", "in_app_chat", "in_app_dictation", "in_app_local_automation",
    "in_app_updates", "in_app_voice", "memories", "fast_mode", "ultrafast_mode", "auth_elicitation",
    "api_key_model_discovery", "enable_request_compression", "unbounded_connection_retries",
    "system_proxy_fallback", "mentions_v2")


def toml_value(value: object) -> str:
    """Return ``value`` as an inline TOML value (strings as JSON-escaped basic strings, which TOML accepts)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}={toml_value(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"no TOML form for {type(value).__name__}")


def config_overrides(recipe: str, tools: tuple[str, ...]) -> dict[str, object]:
    """Return the ``-c`` overrides (key -> value) of a run: provider, MCP server, instructions, side channels off.

    Args:
        recipe: The agent definition's absolute in-container path (``model_instructions_file``).
        tools: The vulnscan MCP tool names (``enabled_tools``).
    """
    provider = {"name": "vulnscan gateway", "base_url": f"{GATEWAY_URL}/v1", "wire_api": "responses",
                "env_key": "OPENAI_API_KEY", "requires_openai_auth": False, "supports_websockets": False,
                "supports_standalone_web_search": False, "request_max_retries": 2, "stream_max_retries": 2,
                "capabilities": {"external_web_access": False, "remote_compaction": "unsupported"}}
    server = {"command": PYTHON, "args": MCP_COMMAND.split()[1:], "env": MCP_ENV, "required": True,
              "enabled_tools": list(tools), "default_tools_approval_mode": "auto"}
    return {"approval_policy": "never", "model_provider": PROVIDER, f"model_providers.{PROVIDER}": provider,
            "mcp_servers.vulnscan": server, "model_instructions_file": recipe, "project_doc_max_bytes": 0,
            "log_dir": f"{CODEX_STATE}/log", "sqlite_home": f"{CODEX_STATE}/sqlite",
            "project_root_markers": [], "web_search": "disabled", "history.persistence": "none",
            "analytics.enabled": False, "feedback.enabled": False, "check_for_update_on_startup": False,
            "agents.enabled": False, "skills.include_instructions": False, "skills.bundled.enabled": False,
            "tools.update_plan.enabled": False, "tools.experimental_request_user_input.enabled": False,
            "include_apps_instructions": False, "notify": [], "shell_environment_policy.inherit": "none"}


def vulnscan_tool_names() -> tuple[str, ...]:
    """Return the vulnscan MCP tool names (sorted), as ``enabled_tools`` lists them."""
    from agentteams.vulnscan import (
        mcp,  # lazy: the schemas pull in nothing heavy, but keep import order simple
    )

    return tuple(sorted(mcp.SCHEMAS))


_CODEX_PROBE = r'''# codex: CODEX_HOME fresh (empty tmpfs) and the state dir writable; a headless run against a local stub shows
# the lockdown and the residual (rev. 3, 7, D4)
_home = os.environ.get("CODEX_HOME", "")
r["codex_home_fresh"] = _home == home and not any(os.path.lexists(os.path.join(_home, n)) for n in %r)
r["codex_state_writable"] = can_mkdir(%r)
from agentteams.vulnscan import selftest_cli as _cli
_checks, _detail = _cli.codex_checks(json.loads(%r), recipe=%r, argv_url=%r)
r.update(_checks)
print("SELFTEST-DETAIL " + json.dumps(_detail))'''


@dataclass(frozen=True)
class CodexDriver(Driver):
    """Codex CLI: ``codex exec --json`` over the gateway's Responses route, MCP and config via ``-c`` (see module).

    ``probe_model`` is ``agent.model`` (set by :func:`agentteams.vulnscan.agent_drivers.driver_for`): the
    self-test runs with it, so the record binds the model whose tool set it saw.
    """

    probe_model: str = ""

    def env(self, a: dict[str, Any], gw_url: str) -> dict[str, str]:
        """See :meth:`Driver.env`: the dummy key and every config home in the tmpfs home or the state dir.

        The provider URL is in the command (:data:`GATEWAY_URL`); ``OPENAI_BASE_URL`` is set to the gateway too,
        so nothing that reads it can reach another host."""
        return {"OPENAI_API_KEY": DUMMY_KEY, "OPENAI_BASE_URL": f"{gw_url}/v1", "CODEX_HOME": CODEX_HOME,
                "XDG_CONFIG_HOME": f"{AGENT_HOME}/.config", "NO_COLOR": "1"}

    def state_dirs(self) -> list[str]:
        """See :meth:`Driver.state_dirs`."""
        return [CODEX_STATE]

    def command(self, a: dict[str, Any], *, recipe: str, session: str, task: str, run_id: str) -> list[str]:
        """See :meth:`Driver.command`. Flags first, the prompt last (``codex exec [OPTIONS] [PROMPT]``)."""
        overrides = config_overrides(recipe, vulnscan_tool_names())
        disabled = [x for f in DISABLED_FEATURES for x in ("--disable", f)]
        return ["codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                "--ignore-rules", "--strict-config", "--color", "never", "-s", "read-only",
                "-m", str(a.get("model") or "{model}"),
                *(x for k, v in overrides.items() for x in ("-c", f"{k}={toml_value(v)}")), *disabled, task]

    def mcp_attach(self) -> McpAttach:
        """See :meth:`Driver.mcp_attach`."""
        return McpAttach("codex-config-override", MCP_COMMAND)

    def parse_outcome(self, transcript: str) -> Outcome:
        """See :meth:`Driver.parse_outcome`: ``turn.failed`` / ``error`` events, else ``turn.completed`` is success.

        ``item.completed`` items of type ``error`` are warnings (e.g. unknown model metadata) and only reported
        when nothing else explains a failure. ``model_calls`` is None: Codex reports usage per turn, not per call.
        """
        failed = error = warning = other = ""
        completed = False
        for line in transcript.splitlines():
            ev = json_or_none(line.strip()) if line.lstrip().startswith("{") else None
            if not isinstance(ev, dict):
                other = line.strip() if "error" in line.lower() else other
                continue
            kind = ev.get("type")
            if kind == "turn.failed":
                err = ev.get("error")
                failed = str(err.get("message") if isinstance(err, dict) else err)
            elif kind == "error":
                error = str(ev.get("message") or "")
            elif kind == "turn.completed":
                completed = True
            elif kind == "item.completed" and isinstance(ev.get("item"), dict) and ev["item"].get("type") == "error":
                warning = str(ev["item"].get("message") or "")
        if failed:
            return Outcome(f"codex turn failed: {failed}"[:300])
        if error:
            return Outcome(f"codex error: {error}"[:300])
        if completed:
            return Outcome("")
        return Outcome((other or warning or "codex printed no turn.completed event")[:300])

    def selftest_entries(self, command: list[str] | None = None) -> list[str]:
        """See :meth:`Driver.selftest_entries`: ``codex_home_fresh``, ``codex_state_writable`` and the headless probe
        (:func:`agentteams.vulnscan.selftest_cli.codex_checks`: ``codex_headless_ran``, ``codex_mcp_attached``,
        ``codex_tool_inventory``, ``codex_canary_inert``, ``codex_residual_write_blocked``). Codex takes no
        ``agent.command`` override (:meth:`command_problem`), so the probe always runs the driver's command,
        with ``agent.model``."""
        argv = self.command({"model": self.probe_model or PROBE_MODEL}, recipe="{recipe}", session="vulnscan-selftest",
                            task="Reply with the single word ok.", run_id="selftest")
        recipe = f"{TEAM_IN_CONTAINER}/{self.selected_recipe()}"   # the file a run loads (agent.recipe)
        return [_CODEX_PROBE % (CODEX_HOME_READS, CODEX_STATE, json.dumps(argv), recipe, GATEWAY_URL)]

    def command_problem(self, command: list[str]) -> str:
        """See :meth:`Driver.command_problem`: no override at all (as claude): the lockdown is the command."""
        if command:
            return ("agent.command overrides are not supported for framework codex: its lockdown is part of the "
                    "driver (agent_drivers_codex.CodexDriver.command); use agent.model instead")
        return ""

    def settings_problem(self, a: dict[str, Any]) -> str:
        """See :meth:`Driver.settings_problem`: the residual ``apply_patch`` must be accepted explicitly (D4)."""
        accepted = a.get("accept_residual_tools") or []
        missing = [t for t in RESIDUAL_TOOLS if t not in accepted]
        if missing:
            return (f"agent.framework codex keeps a residual built-in tool no switch removes ({', '.join(missing)}; "
                    "its writes are rejected by the read-only sandbox, which the self-test checks): set "
                    f"agent.accept_residual_tools: {json.dumps(list(RESIDUAL_TOOLS))} to accept it (decision D4)")
        return ""

    def debrief(self) -> DebriefSupport | None:
        """See :meth:`Driver.debrief`: none (``--ephemeral`` keeps no session; review says so)."""
        return None


CODEX = CodexDriver(
    name="codex", dialect="responses", default_recipe=".codex/agents/vuln-remediation.toml",
    key_env="OPENAI_API_KEY", model_env="",
    image=ImageRecipe(
        # The newest release past the 14-day cooldown (Rule S-10) when pinned: rust-v0.162.1, published
        # 2026-10-09T19:44Z. Digests of codex-<arch>-unknown-linux-musl.tar.gz from GitHub's release assets
        # (api.github.com/repos/openai/codex/releases/tags/rust-v0.162.1, assets[].digest), 2026-10-24. The
        # binary inside is signed (codex-<arch>-unknown-linux-musl.sigstore: Fulcio certificate for
        # .../openai/codex/.github/workflows/rust-release.yml@refs/tags/rust-v0.162.1, issuer
        # token.actions.githubusercontent.com); the aarch64 signature was checked against the extracted binary.
        cli="codex", version="0.162.1", install="cli-codex",
        sha256=(("amd64", "86f268d81b898f3e144c5ecff2ad2fda2c5802e042d6195b72538fe0fa125057"),
                ("arm64", "bf3ca2719f1143b1440fbd85c149379e696c782c50ad7991f8bfcec60b2600c1")),
        checksum_source="https://api.github.com/repos/openai/codex/releases/tags/rust-v{version} assets[].digest "
                        "(+ codex-<arch>-unknown-linux-musl.sigstore)"))
