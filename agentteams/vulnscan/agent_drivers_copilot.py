"""The GitHub Copilot CLI runtime driver (vulnscan-agent-runtimes plan, phase 4; split from :mod:`.agent_drivers`).

``copilot -p`` with bring-your-own-key (BYOK) over the gateway, offline (``COPILOT_OFFLINE=true``: no GitHub
authentication, telemetry, web tools, GitHub MCP server or auto-update), the vulnscan MCP server attached with
``--additional-mcp-config``. Flags and variables from the vendor docs (2026-10-24:
https://docs.github.com/en/copilot/reference/cli-command-reference, .../cli-programmatic-reference,
.../customize-copilot/use-byok-models) and the pinned release's own ``copilot help`` / ``help environment`` /
``help providers`` (1.0.95, its macOS build, offline), and observed by running that build against a loopback
stub (2026-10-24):

- BYOK: ``COPILOT_PROVIDER_BASE_URL`` (``<gateway>/api/v1`` for ``openai``: the CLI posts
  ``/api/v1/chat/completions``; ``<gateway>`` for ``anthropic``: ``/v1/messages``), ``COPILOT_PROVIDER_TYPE``
  (``agent.copilot_provider_type``), ``COPILOT_PROVIDER_API_KEY`` = the dummy key, ``COPILOT_MODEL`` and
  ``COPILOT_PROVIDER_MAX_OUTPUT_TOKENS`` (= ``agent.max_output_tokens``, the gateway's ceiling; it is the
  ``max_tokens`` the CLI sends);
- ``COPILOT_HOME`` (settings, session state, ``providers.json``, ``mcp-config.json``) is a directory of the tmpfs
  home, so it is empty at every start (``copilot_home_fresh``); only the logs go to the host-backed state dir
  (``--log-dir``), where nothing is read as configuration (@security rc.4 review, Medium 2);
- ``COPILOT_CACHE_HOME`` is :data:`COPILOT_CACHE`, in the image: the binary unpacks its bundled package there
  (about 190 MB) on first start, so the image build runs it once (``copilot --version``) and a run reads it
  read-only (root-owned, read-only rootfs). Observed with the pinned linux-arm64 build (2026-10-24): a headless
  run against a stub succeeds with that cache mounted read-only, and an empty read-only cache fails at its
  ``mkdir``, so a run never unpacks code anywhere it could write;
- tools: ``--available-tools`` names only the vulnscan MCP tools (``vulnscan-<tool>``), ``--excluded-tools``
  names every built-in of the pinned release as well (:data:`BUILTIN_TOOLS`; the CLI reports unknown names and
  the tools it disabled as ``session.info`` events), ``--disable-builtin-mcps`` drops ``github-mcp-server`` and
  ``githubiq``, ``--no-ask-user`` drops ``ask_user``, ``--no-custom-instructions`` loads no ``AGENTS.md`` and
  the like; ``--allow-tool=vulnscan`` pre-approves the vulnscan server's tools only;
- prompt mode loads no workspace MCP servers, repository hooks or extensions (they need a trusted folder or an
  opt-in variable, never set here), and the working directory is the empty tmpfs anyway;
- the agent definition: ``--agent=<name> --add-dir=<team>``, which loads the team's ``.github/agents`` (and its
  ``.github/skills``, which stay unused: the ``skill`` tool is excluded); ``--attachment`` takes images and
  documents only, not Markdown. ``agent.recipe`` must therefore be ``.github/agents/<name>.agent.md``. Its YAML
  front matter is configuration (``tools``, ``model``, ``mcp-servers`` ...), so a file with any key but ``name``
  and ``description`` is refused at run time and fails the self-test (``copilot_agent_file_clean``), and the
  file's sha256 is part of the flags fingerprint (:mod:`.agent_recipe`; @security rc.4 review, Medium 1).

The tool inventory self-test (:func:`agentteams.vulnscan.selftest_cli.copilot_checks`) is the real guard: a
built-in this list misses, or one a later release adds, shows up in the requests the stub receives and fails it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from agentteams.vulnscan import agent_upstreams
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

#: The host-backed writable mount (``<run>/agent/copilot-data``): the CLI's logs only (``--log-dir``).
COPILOT_STATE = f"{AGENT_HOME}/.copilot-state"
#: ``COPILOT_HOME``: settings and session state, in the tmpfs home (empty at every start: ``copilot_home_fresh``).
COPILOT_HOME = f"{AGENT_HOME}/.copilot"
#: ``COPILOT_CACHE_HOME``: the unpacked package, populated by the image build (Dockerfile ``cli-copilot``), read-only.
COPILOT_CACHE = "/opt/copilot-cache"
MCP_SERVER = "vulnscan"
#: Every built-in tool of the pinned release: the docs' "Tool availability values" (less ``write_bash`` and
#: ``write_powershell``, which 1.0.95 reports as unknown) plus the ones its default inventory showed
#: (``fetch_copilot_cli_documentation``, ``sql``, ``session_store_sql``, ``tool_search_tool``, ``update_todo``).
BUILTIN_TOOLS = ("bash", "list_bash", "read_bash", "stop_bash", "powershell", "list_powershell", "read_powershell",
                 "stop_powershell", "apply_patch", "create", "edit", "view", "list_agents", "read_agent", "task",
                 "write_agent", "ask_user", "glob", "grep", "rg", "skill", "web_fetch",
                 "fetch_copilot_cli_documentation", "sql", "session_store_sql", "tool_search_tool", "update_todo")
#: ``agent.recipe`` for copilot: a custom agent file directly under the team's ``.github/agents``.
RECIPE_RE = re.compile(r"\.github/agents/([A-Za-z0-9_-]+)(?:\.agent)?\.md")
#: The model the self-test names (a local stub answers; nothing is billed) and its output setting.
PROBE = {"model": "vulnscan-selftest", "max_output_tokens": 32_000}


def mcp_tool_names() -> tuple[str, ...]:
    """Return the vulnscan MCP tool names as Copilot names them for the model (``<server>-<tool>``)."""
    from agentteams.vulnscan import (
        mcp,  # lazy: keeps the driver modules' import graph flat
    )

    return tuple(f"{MCP_SERVER}-{name}" for name in sorted(mcp.SCHEMAS))


#: ``--additional-mcp-config`` takes JSON inline (or ``@file``); inline, it is part of the hashed command.
MCP_CONFIG = json.dumps({"mcpServers": {MCP_SERVER: {
    "type": "local", "command": PYTHON, "args": MCP_COMMAND.split()[1:], "env": MCP_ENV, "tools": ["*"],
    "deferTools": "never", "disableToolCache": True}}}, sort_keys=True, separators=(",", ":"))


def lockfile_version() -> str:
    """Return the ``@github/copilot`` version the committed lockfile pins (the image builds only that one)."""
    lock = json.loads((resources.files("agentteams") / "data" / "vulnscan_agent" / "copilot" /
                       "package-lock.json").read_text(encoding="utf-8"))
    return str(lock["packages"]["node_modules/@" + "github/copilot"]["version"])


def agent_location(recipe: str) -> tuple[str, str]:
    """Return ``(team root, agent name)`` for an absolute agent-definition path ``<root>/.github/agents/<name>.agent.md``.

    Raises:
        ValueError: The path is not a custom agent file under ``.github/agents``.
    """
    root, sep, rel = recipe.rpartition("/.github/agents/")
    match = RECIPE_RE.fullmatch(".github/agents/" + rel) if sep else None
    if not match:
        raise ValueError(f"copilot runs a custom agent file .github/agents/<name>.agent.md (got {recipe[-80:]!r})")
    return root, match.group(1)


_COPILOT_PROBE = r'''# copilot: COPILOT_HOME fresh (tmpfs: absent or empty), the code cache populated and read-only, the log dir
# writable; a headless run against a local stub shows the lockdown (rev. 3, 7; @security rc.4 Medium 2)
from agentteams.vulnscan import selftest_cli as _cli
r["copilot_home_fresh"] = _cli.copilot_home_fresh(os.environ, home, %r, %r)
r["copilot_state_writable"] = can_mkdir(%r)
_checks, _detail = _cli.copilot_checks(json.loads(%r), json.loads(%r), recipe=%r, dialect=%r)
r.update(_checks)
print("SELFTEST-DETAIL " + json.dumps(_detail))'''


@dataclass(frozen=True)
class CopilotDriver(Driver):
    """Copilot CLI: ``copilot -p`` with BYOK through the gateway, offline, MCP via ``--additional-mcp-config``.

    The dialect is ``chat`` (``COPILOT_PROVIDER_TYPE=openai``) or ``messages`` (``anthropic``), chosen by
    ``agent.copilot_provider_type`` (:func:`agentteams.vulnscan.agent_drivers.driver_for`).
    """

    def provider(self) -> tuple[str, str]:
        """Return ``(COPILOT_PROVIDER_TYPE, base URL suffix)`` for the dialect: the CLI appends
        ``/chat/completions`` (openai) or ``/v1/messages`` (anthropic) to the base URL."""
        return ("anthropic", "") if self.dialect == "messages" else ("openai", "/api/v1")

    def env(self, a: dict[str, Any], gw_url: str) -> dict[str, str]:
        """See :meth:`Driver.env`: BYOK at the gateway with the dummy key, offline, every home in the state dir."""
        kind, suffix = self.provider()
        return {"COPILOT_PROVIDER_BASE_URL": gw_url + suffix, "COPILOT_PROVIDER_TYPE": kind,
                "COPILOT_PROVIDER_API_KEY": DUMMY_KEY, "COPILOT_OFFLINE": "true", "COPILOT_AUTO_UPDATE": "false",
                "COPILOT_HOME": COPILOT_HOME, "COPILOT_CACHE_HOME": COPILOT_CACHE,
                "COPILOT_MCP_TOOL_CACHE": "false", "XDG_CONFIG_HOME": f"{AGENT_HOME}/.config",
                "XDG_STATE_HOME": f"{AGENT_HOME}/.local/state", "NO_COLOR": "1"}

    def run_env(self, a: dict[str, Any]) -> dict[str, str]:
        """See :meth:`Driver.run_env`: ``COPILOT_MODEL`` and the output setting equal to the gateway's ceiling."""
        return {"COPILOT_MODEL": str(a["model"]),
                "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS": str(agent_upstreams.max_output_tokens(a))}

    def state_dirs(self) -> list[str]:
        """See :meth:`Driver.state_dirs`."""
        return [COPILOT_STATE]

    def command(self, a: dict[str, Any], *, recipe: str, session: str, task: str, run_id: str) -> list[str]:
        """See :meth:`Driver.command`. The custom agent is loaded from the team (``--agent`` + ``--add-dir``); the
        model comes from ``COPILOT_MODEL`` (:meth:`run_env`). List options use the ``--flag=a,b`` form (they
        take several values)."""
        team, name = agent_location(recipe)
        return ["copilot", "-p", task, f"--agent={name}", f"--add-dir={team}", "--output-format", "json", "-s",
                "--no-ask-user", f"--allow-tool={MCP_SERVER}", f"--available-tools={','.join(mcp_tool_names())}",
                f"--excluded-tools={','.join(BUILTIN_TOOLS)}", "--disable-builtin-mcps",
                f"--additional-mcp-config={MCP_CONFIG}", "--no-custom-instructions", "--no-auto-update",
                "--disallow-temp-dir", "--no-color", "--stream", "on", f"--log-dir={COPILOT_STATE}/logs"]

    def command_template(self) -> list[str]:
        """See :meth:`Driver.command_template` (the selected agent definition under ``{team}``)."""
        return self.command({}, recipe=f"{{team}}/{self.selected_recipe()}", session="{session}", task="{task}",
                            run_id="{run_id}")

    def mcp_attach(self) -> McpAttach:
        """See :meth:`Driver.mcp_attach`."""
        return McpAttach("copilot-additional-mcp-config", MCP_COMMAND)

    def parse_outcome(self, transcript: str) -> Outcome:
        """See :meth:`Driver.parse_outcome`: from the JSONL stream (observed with 1.0.95).

        ``session.error`` (``data.message``) is the CLI's error; the final ``result`` event carries the
        ``exitCode``; ``model_calls`` counts ``model.call_finished`` events (None when there are none and no
        ``result``). Without a ``result``, the last line mentioning an error, else the last plain line (the CLI
        prints start-up refusals such as ``No such agent: ...`` that way)."""
        error = other = plain = ""
        calls, result = 0, None
        for line in transcript.splitlines():
            ev = json_or_none(line.strip()) if line.lstrip().startswith("{") else None
            if not isinstance(ev, dict):
                other = line.strip() if "error" in line.lower() else other
                plain = line.strip() or plain
                continue
            kind, data = str(ev.get("type") or ""), ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if kind.endswith("error"):
                error = str(data.get("message") or data.get("errorType") or kind)
            elif kind == "model.call_finished":
                calls += 1
            elif kind == "result":
                result = ev
        model_calls = calls if (calls or result is not None) else None
        if error:
            return Outcome(f"copilot error: {error}"[:300], model_calls)
        if result is not None and result.get("exitCode") not in (0, None):
            return Outcome(f"copilot exited {result.get('exitCode')}"[:300], model_calls)
        if result is not None:
            return Outcome("", model_calls)
        return Outcome((other or plain or "copilot printed no result event")[:300], model_calls)

    def selftest_entries(self, command: list[str] | None = None) -> list[str]:
        """See :meth:`Driver.selftest_entries`: ``copilot_home_fresh``, ``copilot_state_writable`` and the headless
        probe (:func:`agentteams.vulnscan.selftest_cli.copilot_checks`: ``copilot_agent_file_clean``,
        ``copilot_headless_ran``, ``copilot_mcp_attached``, ``copilot_tool_inventory``, ``copilot_canary_inert``).
        Copilot takes no ``agent.command`` override, so the probe runs the driver's own command (``{team}``: a copy
        the probe makes of the selected agent file, :meth:`Driver.selected_recipe`)."""
        argv = self.command(PROBE, recipe=f"{{team}}/{self.selected_recipe()}", session="vulnscan-selftest",
                            task="Reply with the single word ok.", run_id="selftest")
        recipe = f"{TEAM_IN_CONTAINER}/{self.selected_recipe()}"
        return [_COPILOT_PROBE % (COPILOT_HOME, COPILOT_CACHE, f"{COPILOT_STATE}/logs", json.dumps(argv),
                                  json.dumps(self.run_env(PROBE)), recipe, self.dialect)]

    def command_problem(self, command: list[str]) -> str:
        """See :meth:`Driver.command_problem`: no override at all (as claude): the lockdown is the command."""
        if command:
            return ("agent.command overrides are not supported for framework copilot: its lockdown is part of the "
                    "driver (agent_drivers_copilot.CopilotDriver.command); use agent.model instead")
        return ""

    def settings_problem(self, a: dict[str, Any]) -> str:
        """See :meth:`Driver.settings_problem`: ``agent.recipe`` must name a custom agent file (``--agent``)."""
        recipe = str(a.get("recipe") or self.default_recipe).lstrip("/")
        if not RECIPE_RE.fullmatch(recipe):
            return (f"agent.recipe for framework copilot must be .github/agents/<name>.agent.md in the team "
                    f"(the CLI loads it with --agent/--add-dir; got {recipe[:80]!r})")
        return ""

    def debrief(self) -> DebriefSupport | None:
        """See :meth:`Driver.debrief`: none in this release (review says so)."""
        return None


COPILOT = CopilotDriver(
    name="copilot", dialect="chat", default_recipe=".github/agents/vuln-remediation.agent.md",
    key_env="COPILOT_PROVIDER_API_KEY", model_env="COPILOT_MODEL",
    image=ImageRecipe(
        # The newest release past the 14-day cooldown (Rule S-10) when pinned: 1.0.95, published to npm
        # 2026-10-09T16:04Z (npm view @github/copilot time). No published checksum (plan revision 12): the
        # image runs `npm ci --ignore-scripts` from data/vulnscan_agent/copilot/package-lock.json (sha512 per
        # tarball) and `npm audit signatures` (2026-10-24: 2 registry signatures and 1 attestation verified),
        # and checks this hash over the whole installed tree, per architecture (arm64: computed by npm ci;
        # amd64: the integrity-checked linux-x64 tarball added to the same tree):
        #   find node_modules -type f ! -name .package-lock.json -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum
        cli="copilot", version="1.0.95", install="cli-copilot",
        sha256=(("amd64", "697693b945949ce2ddc2001281476859a9a6d1e2919d7e984046dd575100ab32"),
                ("arm64", "99fe11f508d60a2d9e373e899ad823b41a601b8e4e8483a58d9ae7c1dabb8fba")),
        checksum_source="installed-tree sha256 of `npm ci --ignore-scripts` from the committed lockfile "
                        "(agentteams/data/vulnscan_agent/copilot/package-lock.json)",
        # The npm build stage's image (Node 24.21.0, npm 11.19.0: libc-aware optional dependencies, audit
        # signatures with attestations), pinned by its index digest (built 2026-10-06).
        build_args=(("NODE_IMAGE", ("node:24.21.0-bookworm-slim@sha256:"
                                    "d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20")),)))
