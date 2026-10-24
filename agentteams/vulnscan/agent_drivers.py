"""Runtime drivers for the vulnscan remediation agent: one per agent framework.

``agent.framework`` selects the driver (:func:`get_driver`). A driver says everything that differs between
agent CLIs; :mod:`agentteams.vulnscan.agent_run` keeps everything they share: the isolation flags, the
mounts, the gateway, the run lock and the self-test record. Containment that holds for every driver
(plan revision 7):

- the container's working directory is :data:`CWD_IN_CONTAINER`, an empty tmpfs, never the team mount, so
  no CLI auto-loads instruction, hook or MCP files (``AGENTS.md``, ``.mcp.json``, ``.codex/`` ...) from it;
- the team is mounted read-only at :data:`TEAM_IN_CONTAINER` and the agent definition is passed by its
  absolute path there;
- every config home the CLI reads points into the tmpfs ``$HOME`` (:data:`AGENT_HOME`), empty at start;
- the only writable host-backed dirs are the driver's :meth:`Driver.state_dirs`, under
  ``<run>/agent/<name>-data``.

This release implements the ``goose`` and ``claude`` drivers. ``copilot``, ``codex`` and ``grok`` are known
frameworks whose drivers come in later phases; :func:`get_driver` refuses them by name.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentteams.vulnscan import agent_upstreams

AGENT_HOME = "/agenthome"
#: The container's working directory: an empty tmpfs (revision 7). Not ``/work/team``.
CWD_IN_CONTAINER = "/work/cwd"
TEAM_IN_CONTAINER = "/work/team"
MCP_COMMAND = "python -m agentteams.vulnscan.mcp"
#: The model key every driver's CLI sees; the real key exists only in the gateway.
DUMMY_KEY = "injected-by-gateway"
FRAMEWORKS = ("goose", "claude", "copilot", "codex", "grok")
DIALECTS = ("chat", "messages", "responses")


@dataclass(frozen=True)
class ImageRecipe:
    """How the image's Dockerfile installs the CLI (build arg ``AGENT_CLI`` selects stage ``cli-<cli>``).

    ``sha256`` maps Docker's ``TARGETARCH`` to the digest of the release asset for that architecture, as
    published in ``checksum_source``; the build re-checks the download against the digest it is given.
    """

    cli: str
    version: str
    sha256: tuple[tuple[str, str], ...]
    checksum_source: str
    install: str


@dataclass(frozen=True)
class McpAttach:
    """How the vulnscan MCP server reaches the CLI (folded into :meth:`Driver.command`, env or config files)."""

    kind: str
    command: str


@dataclass(frozen=True)
class Outcome:
    """What a run transcript says: the CLI's own error line (capped, or ``""``) and model-call evidence.

    ``model_calls`` is None when the CLI does not report calls; the gateway's spend record then decides.
    """

    error: str
    model_calls: int | None = None


@dataclass(frozen=True)
class DebriefSupport:
    """How a past run's session is resumed interactively (``resume(session)`` gives the in-container argv)."""

    resume: Callable[[str], list[str]]
    note: str


@dataclass(frozen=True)
class Driver:
    """One agent runtime. Subclasses implement the methods; the fields are data.

    ``key_env`` is the dummy-key variable the self-test checks; ``model_env`` the variable carrying
    ``agent.model`` ("" when :meth:`command` passes the model itself).
    """

    name: str
    dialect: str
    image: ImageRecipe
    default_recipe: str
    key_env: str
    model_env: str

    def env(self, a: dict[str, Any], gw_url: str) -> dict[str, str]:
        """Return the container env for this CLI (dummy key, provider vars, config homes in the tmpfs home).

        ``a`` may be empty (the flags fingerprint and the self-test build the env without the operator's
        settings): per-run values go through ``model_env`` or :meth:`command`.
        """
        raise NotImplementedError

    def state_dirs(self) -> list[str]:
        """Return the in-container dirs the CLI must write (host-backed under ``<run>/agent/<name>-data``)."""
        raise NotImplementedError

    def command(self, a: dict[str, Any], *, recipe: str, session: str, task: str, run_id: str) -> list[str]:
        """Return the CLI argv; ``recipe`` is the agent definition's absolute in-container path."""
        raise NotImplementedError

    def mcp_attach(self) -> McpAttach:
        """Return how the vulnscan MCP server is attached."""
        raise NotImplementedError

    def parse_outcome(self, transcript: str) -> Outcome:
        """Return the error line and model-call evidence from a run transcript."""
        raise NotImplementedError

    def selftest_entries(self, command: list[str] | None = None) -> list[str]:
        """Return probe snippets (Python, run inside the self-test) for checks specific to this runtime.

        ``command`` is the operator's ``agent.command`` override (None or empty: the driver's own): a driver
        whose probe runs the CLI must run exactly the command a run would use.
        """
        raise NotImplementedError

    def command_problem(self, command: list[str]) -> str:
        """Return why an ``agent.command`` override may not be used with this runtime, or ``""`` (default: none)."""
        return ""

    def debrief(self) -> DebriefSupport | None:
        """Return how to resume a run interactively, or None when this runtime has no debrief."""
        return None

    def run_env(self, a: dict[str, Any]) -> dict[str, str]:
        """Return the per-run env from the operator's settings (default: ``model_env`` = ``agent.model``).

        Passed with ``-e`` after the shared flags by a run and a debrief (:func:`run_env_args`); not part of
        :meth:`env`, which the flags fingerprint builds without settings.
        """
        return {self.model_env: str(a["model"])} if self.model_env else {}

    def command_template(self) -> list[str]:
        """Return :meth:`command` with ``{recipe}``, ``{session}``, ``{task}`` and ``{run_id}`` placeholders."""
        return self.command({}, recipe="{recipe}", session="{session}", task="{task}", run_id="{run_id}")


# ---------------------------------------------------------------- goose

#: goose's data dir ($XDG_DATA_HOME/goose): its sessions, the one host-backed writable mount
#: (``<run>/agent/goose-data``), so a debrief can resume the run's session.
GOOSE_SESSIONS = f"{AGENT_HOME}/.local/share/goose"
#: goose's XDG state dir (its logs), inside the sessions mount so the logs survive as
#: ``<run>/agent/goose-data/state/goose/logs``. Not ``$HOME/.local/state``: the sessions bind mount makes Docker
#: create ``$HOME/.local`` and ``.local/share`` owned by root, so the agent uid cannot add ``.local/state`` and
#: goose stops before its first model call (first live run, 2026-10-23).
GOOSE_STATE = f"{GOOSE_SESSIONS}/state"

_GOOSE_DIRS_PROBE = r'''# goose writes logs, sessions and config under its XDG dirs; a dir it cannot create stops it before any model call.
r["goose_dirs_writable"] = all(can_mkdir(os.path.join(os.environ.get(var) or home + dflt, "goose", sub))
                               for var, dflt, sub in (("XDG_STATE_HOME", "/.local/state", "logs"),
                                                      ("XDG_DATA_HOME", "/.local/share", "sessions"),
                                                      ("XDG_CONFIG_HOME", "/.config", "")))'''


@dataclass(frozen=True)
class GooseDriver(Driver):
    """goose: ``goose run --recipe`` over OpenRouter chat completions, the MCP server as a stdio extension.

    goose needs nothing from its working directory: the recipe path is absolute, and no ``developer``
    extension (the one that would read ``.goosehints`` / ``AGENTS.md``) is loaded. Before revision 7 its cwd
    was the image's ``WORKDIR`` ``/work`` (the parent of the mounts), never ``/work/team``.
    """

    def env(self, a: dict[str, Any], gw_url: str) -> dict[str, str]:
        """See :meth:`Driver.env`. ``XDG_CONFIG_HOME`` is goose's default (``$HOME/.config``) made explicit."""
        return {"GOOSE_PROVIDER": "openrouter", "OPENROUTER_HOST": gw_url, "OPENROUTER_API_KEY": DUMMY_KEY,
                "GOOSE_MODE": "auto", "GOOSE_DISABLE_KEYRING": "1",
                "XDG_DATA_HOME": f"{AGENT_HOME}/.local/share", "XDG_STATE_HOME": GOOSE_STATE,
                "XDG_CONFIG_HOME": f"{AGENT_HOME}/.config"}

    def state_dirs(self) -> list[str]:
        """See :meth:`Driver.state_dirs`."""
        return [GOOSE_SESSIONS]

    def command(self, a: dict[str, Any], *, recipe: str, session: str, task: str, run_id: str) -> list[str]:
        """See :meth:`Driver.command`. The session is named so a debrief can resume it; the recipe's prompt is the task."""
        return ["goose", "run", "--recipe", recipe, "--name", session,
                "--with-extension", self.mcp_attach().command, "--params", f"task={task}"]

    def mcp_attach(self) -> McpAttach:
        """See :meth:`Driver.mcp_attach`."""
        return McpAttach("goose-extension", MCP_COMMAND)

    def parse_outcome(self, transcript: str) -> Outcome:
        """See :meth:`Driver.parse_outcome`: the last line mentioning an error (goose exits 0 after one)."""
        hit = next((ln.strip() for ln in reversed(transcript.splitlines()) if "error" in ln.lower()), "")
        return Outcome(error=hit[:300])

    def selftest_entries(self, command: list[str] | None = None) -> list[str]:
        """See :meth:`Driver.selftest_entries`: ``goose_dirs_writable`` (goose's probe runs no CLI command)."""
        return [_GOOSE_DIRS_PROBE]

    def debrief(self) -> DebriefSupport:
        """See :meth:`Driver.debrief`: resume by session name, re-passing the MCP extension."""
        return DebriefSupport(
            resume=lambda session: ["goose", "session", "--resume", "--name", session,
                                    "--with-extension", MCP_COMMAND],
            note="goose session --resume --name <session> (see debrief.verify_goose_sessions)")


GOOSE = GooseDriver(
    name="goose", dialect="chat", default_recipe=".goose/recipes/vuln-remediation.yaml",
    key_env="OPENROUTER_API_KEY", model_env="GOOSE_MODEL",
    image=ImageRecipe(
        # The newest release past the 14-day cooldown (Rule S-10) when pinned: v1.54.0, published 2026-10-08.
        # Digests of goose-<arch>-unknown-linux-gnu.tar.bz2 from GitHub's release assets
        # (gh api repos/block/goose/releases/tags/v1.54.0, assets[].digest), 2026-10-23.
        cli="goose", version="1.54.0", install="cli-goose",
        sha256=(("amd64", "bf01cb6359f7a2b1acb54ca4827e1d059b18aabb3d2b75202e21066cfd3a3396"),
                ("arm64", "8c7ad233bdaf05f10a52ef36f3ce50f839cb1d2d1df5b31b172af7150c1c288e")),
        checksum_source="https://api.github.com/repos/block/goose/releases/tags/v{version} assets[].digest"))

# ---------------------------------------------------------------- claude (Claude Code)

#: Claude Code's config dir (``CLAUDE_CONFIG_DIR``: settings, ``.claude.json``, session transcripts under
#: ``projects/``), the one host-backed writable mount (``<run>/agent/claude-data``), so a debrief can resume.
CLAUDE_STATE = f"{AGENT_HOME}/.claude-state"
#: The in-container python the MCP server runs with (absolute: no PATH lookup).
PYTHON = "/usr/local/bin/python"
#: The vulnscan MCP server's env, as the container sets it (``agent_run._base_argv``), restated so the
#: server sees it whether or not the CLI passes its own env to stdio servers.
MCP_ENV = {"VULNSCAN_RUN_DIR": "/work/run", "VULNSCAN_SPOOL": "/work/spool/requests",
           "VULNSCAN_RESULTS": "/work/spool/results"}
#: ``--mcp-config`` takes a file or an inline JSON string (cli-reference); inline, it is part of the command
#: the flags fingerprint hashes, and no file has to be written before the run.
CLAUDE_MCP_CONFIG = json.dumps({"mcpServers": {"vulnscan": {
    "type": "stdio", "command": PYTHON, "args": ["-m", "agentteams.vulnscan.mcp"], "env": MCP_ENV}}},
    sort_keys=True, separators=(",", ":"))
#: Allow rule for every vulnscan MCP tool (permissions: ``mcp__<server>__*``); everything else that would
#: need approval is denied by ``--permission-mode dontAsk``.
CLAUDE_ALLOW_MCP = "mcp__vulnscan__*"

_CLAUDE_PROBE = r'''# claude: its config dir writable; a headless run against a local stub shows the lockdown (rev. 3, 7)
r["claude_config_writable"] = can_mkdir(os.environ.get("CLAUDE_CONFIG_DIR", ""))
from agentteams.vulnscan import selftest_canary as _canary
_checks, _detail = _canary.claude_checks(json.loads(%r), json.loads(%r), recipe=%r)
r.update(_checks)
print("SELFTEST-DETAIL " + json.dumps(_detail))'''


def _json_or_none(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class ClaudeDriver(Driver):
    """Claude Code: ``claude --bare -p`` over the gateway's Anthropic Messages route, MCP via ``--mcp-config``.

    Containment (cli-reference and headless docs, https://code.claude.com/docs/en/cli-reference,
    https://code.claude.com/docs/en/headless): ``--bare`` skips auto-discovery of hooks, skills, plugins,
    MCP servers, auto memory and CLAUDE.md and reads no OAuth or keychain credential (only
    ``ANTHROPIC_API_KEY``, here the dummy); ``--tools ""`` removes every built-in tool; ``--strict-mcp-config``
    loads only the ``--mcp-config`` server; ``--allowedTools mcp__vulnscan__*`` pre-approves its tools and
    ``--permission-mode dontAsk`` denies anything else that would prompt; ``--disable-slash-commands`` turns
    skills and commands off. The agent definition is appended to the system prompt by its absolute path
    (``--append-system-prompt-file``), the prompt is the task. The session is named (``--name``) so a
    debrief can resume it (``--resume <name>``).
    """

    def env(self, a: dict[str, Any], gw_url: str) -> dict[str, str]:
        """See :meth:`Driver.env`: the gateway as the API base, the dummy key, every update and side channel off."""
        return {"ANTHROPIC_BASE_URL": gw_url, "ANTHROPIC_API_KEY": DUMMY_KEY, "CLAUDE_CONFIG_DIR": CLAUDE_STATE,
                "XDG_CONFIG_HOME": f"{AGENT_HOME}/.config", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1", "DISABLE_TELEMETRY": "1",
                "DISABLE_ERROR_REPORTING": "1", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "ENABLE_TOOL_SEARCH": "false"}

    def run_env(self, a: dict[str, Any]) -> dict[str, str]:
        """See :meth:`Driver.run_env`: every model alias and helper pinned to ``agent.model`` (one-model pin,
        revision 11), and the output setting equal to the gateway's ceiling (``agent.max_output_tokens``)."""
        model = str(a["model"])
        pinned = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
                  "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")
        return {**dict.fromkeys(pinned, model),
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(agent_upstreams.max_output_tokens(a))}

    def state_dirs(self) -> list[str]:
        """See :meth:`Driver.state_dirs`."""
        return [CLAUDE_STATE]

    def command(self, a: dict[str, Any], *, recipe: str, session: str, task: str, run_id: str) -> list[str]:
        """See :meth:`Driver.command`. The prompt follows ``-p`` directly: ``--mcp-config``, ``--tools`` and
        ``--allowedTools`` take several values, so each is followed by another flag, never by the prompt."""
        return ["claude", "--bare", "-p", task, "--name", session, "--append-system-prompt-file", recipe,
                "--tools", "", "--strict-mcp-config", "--mcp-config", CLAUDE_MCP_CONFIG,
                "--allowedTools", CLAUDE_ALLOW_MCP, "--permission-mode", "dontAsk", "--disable-slash-commands",
                "--output-format", "stream-json", "--verbose",
                "--max-turns", str(agent_upstreams.max_turns(a)), "--model", str(a.get("model") or "{model}")]

    def mcp_attach(self) -> McpAttach:
        """See :meth:`Driver.mcp_attach`."""
        return McpAttach("claude-mcp-config", MCP_COMMAND)

    def parse_outcome(self, transcript: str) -> Outcome:
        """See :meth:`Driver.parse_outcome`: the stream-json ``result`` event (``is_error``, ``subtype``), else the
        last ``system/api_retry`` error, else the last non-JSON line mentioning an error. ``model_calls``
        counts distinct assistant message ids (None when the stream has no assistant or result event)."""
        result: dict[str, Any] | None = None
        retry: dict[str, Any] | None = None
        ids: set[str] = set()
        anonymous, other = 0, ""
        for line in transcript.splitlines():
            ev = _json_or_none(line.strip()) if line.lstrip().startswith("{") else None
            if not isinstance(ev, dict):
                other = line.strip() if "error" in line.lower() else other
            elif ev.get("type") == "result":
                result = ev
            elif ev.get("type") == "system" and ev.get("subtype") == "api_retry":
                retry = ev
            elif ev.get("type") == "assistant":
                msg = ev.get("message")
                mid = msg.get("id") if isinstance(msg, dict) else None
                if isinstance(mid, str):
                    ids.add(mid)
                else:
                    anonymous += 1
        calls = len(ids) + anonymous if (ids or anonymous or result is not None) else None
        if result is not None:
            if not result.get("is_error") and result.get("subtype") == "success":
                return Outcome("", calls)
            errors = result.get("errors")
            detail = "; ".join(map(str, errors)) if isinstance(errors, list) and errors else str(result.get("result") or "")
            return Outcome(f"claude {result.get('subtype') or 'error'}: {detail}"[:300], calls)
        if retry is not None:
            return Outcome(f"claude API error {retry.get('error')} (HTTP {retry.get('error_status')})"[:300], calls)
        return Outcome((other or "claude printed no result event")[:300], calls)

    def selftest_entries(self, command: list[str] | None = None) -> list[str]:
        """See :meth:`Driver.selftest_entries`: ``claude_config_writable`` and the headless probe
        (:func:`agentteams.vulnscan.selftest_canary.claude_checks`: ``claude_headless_ran``,
        ``claude_mcp_attached``, ``claude_tool_inventory``, ``claude_canary_inert``). The probe runs the
        ``agent.command`` override when one is set (placeholders filled as a run fills them, ``{recipe}``
        by the probe), so a passing self-test vouches for the command a run uses, never for another."""
        probe = {"model": SELFTEST_MODEL, "max_turns": 2, "max_output_tokens": 1024}
        session, task, run_id = "vulnscan-selftest", "Reply with the single word ok.", "selftest"
        if command:
            argv = [part.replace("{session}", session).replace("{run_id}", run_id).replace("{task}", task)
                    for part in command]
        else:
            argv = self.command(probe, recipe="{recipe}", session=session, task=task, run_id=run_id)
        recipe = f"{TEAM_IN_CONTAINER}/{self.default_recipe}"
        return [_CLAUDE_PROBE % (json.dumps(argv), json.dumps(self.run_env(probe)), recipe)]

    def command_problem(self, command: list[str]) -> str:
        """See :meth:`Driver.command_problem`: claude takes no ``agent.command`` override at all.

        Its lockdown (``--bare``, ``--tools ""``, ``--strict-mcp-config``, the one vulnscan MCP server, the
        ``mcp__vulnscan__*`` allow rule, ``dontAsk``) is part of the driver. A flag blocklist cannot keep up with
        the CLI (a wrapper program, a second ``--mcp-config`` whose stdio command runs at start-up, new flags), and
        the self-test can only judge output a wrapper could fake (@security, rc.3 re-check)."""
        if command:
            return ("agent.command overrides are not supported for framework claude: its lockdown is part of the "
                    "driver (agent_drivers.ClaudeDriver.command); use agent.model / agent.max_turns instead")
        return ""

    def debrief(self) -> DebriefSupport:
        """See :meth:`Driver.debrief`: resume the named session interactively, same lockdown (unverified live)."""
        return DebriefSupport(
            resume=lambda session: ["claude", "--bare", "--resume", session, "--tools", "", "--strict-mcp-config",
                                    "--mcp-config", CLAUDE_MCP_CONFIG, "--allowedTools", CLAUDE_ALLOW_MCP,
                                    "--permission-mode", "dontAsk", "--disable-slash-commands"],
            note="claude --bare --resume <session> (the request spool is read-only in a debrief)")


#: The model id the self-test's headless run names (a local stub answers; nothing is billed).
SELFTEST_MODEL = "claude-vulnscan-selftest"


CLAUDE = ClaudeDriver(
    name="claude", dialect="messages", default_recipe=".claude/agents/vuln-remediation.md",
    key_env="ANTHROPIC_API_KEY", model_env="ANTHROPIC_MODEL",
    image=ImageRecipe(
        # The newest release past the 14-day cooldown (Rule S-10) when pinned: 2.1.296, published to npm
        # 2026-10-09T16:58Z (npm view @anthropic-ai/claude-code time). Native linux binaries, digests from the
        # release's manifest.json (platforms.linux-x64 / linux-arm64 .checksum), whose detached signature
        # manifest.json.sig verified (gpg: "Good signature", Anthropic Claude Code Release Signing, key
        # 31DD DE24 DDFA B679 F42D 7BD2 BAA9 29FF 1A7E CACE), 2026-10-23.
        cli="claude", version="2.1.296", install="cli-claude",
        sha256=(("amd64", "24972e3bc859fab2b46ed4c1e51f7d6130f06d3bd550811a114640de3370d0de"),
                ("arm64", "f1f6e96e0d8342b9dbf41d7e88255397a6a52ce3d8736ad6a4c6b59c9b62fefa")),
        checksum_source="https://downloads.claude.ai/claude-code-releases/{version}/manifest.json "
                        "platforms.linux-<x64|arm64>.checksum (GPG-signed: manifest.json.sig)"))

DRIVERS: dict[str, Driver] = {"goose": GOOSE, "claude": CLAUDE}


def run_env_args(driver: Driver, a: dict[str, Any]) -> list[str]:
    """Return :meth:`Driver.run_env` as ``docker run -e`` arguments (goose: ``-e GOOSE_MODEL=<model>``)."""
    return [x for k, v in driver.run_env(a).items() for x in ("-e", f"{k}={v}")]


def get_driver(name: str) -> Driver:
    """Return the driver for ``agent.framework``.

    Args:
        name: The framework name.

    Returns:
        The driver.

    Raises:
        ValueError: ``name`` is not a known framework, or its driver is not in this release.
    """
    if name in DRIVERS:
        return DRIVERS[name]
    if name in FRAMEWORKS:
        raise ValueError(f"agent.framework {name}: driver not available in this release")
    raise ValueError(f"agent.framework must be one of {', '.join(FRAMEWORKS)} (got {repr(name)[:80]})")
