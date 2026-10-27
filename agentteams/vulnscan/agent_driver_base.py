"""Shared pieces of the vulnscan runtime drivers: the :class:`Driver` contract and the container constants.

Split from :mod:`.agent_drivers` (which re-exports everything here) so the per-framework modules
(:mod:`.agent_drivers_copilot`, :mod:`.agent_drivers_codex`) build on it without an import cycle.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

AGENT_HOME = "/agenthome"
#: The container's working directory: an empty tmpfs (revision 7). Not ``/work/team``.
CWD_IN_CONTAINER = "/work/cwd"
TEAM_IN_CONTAINER = "/work/team"


def _names_team(part: str) -> bool:
    """Return whether a command word names the team mount: any path in it (after ``=`` or ``:``), normalized."""
    for path in re.findall(r"/[^\s=:,;]*", part):
        norm = posixpath.normpath("/" + path.lstrip("/"))   # /work/./team, //work/team, /work/x/../team
        if norm == TEAM_IN_CONTAINER or norm.startswith(TEAM_IN_CONTAINER + "/"):
            return True
    return False


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
    build_args: tuple[tuple[str, str], ...] = ()     # further pinned build args (copilot: NODE_IMAGE)

    def build_argv(self) -> list[str]:
        """Return :attr:`build_args` as ``docker build --build-arg K=V`` arguments."""
        return [x for k, v in self.build_args for x in ("--build-arg", f"{k}={v}")]


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
    ``agent.model`` ("" when :meth:`command` passes the model itself). ``recipe`` is ``agent.recipe`` as
    configured ("": :attr:`default_recipe`), set by :func:`agentteams.vulnscan.agent_drivers.driver_for` so
    the self-test runs with the file a run loads (:meth:`selected_recipe`; :mod:`.agent_recipe`).
    """

    name: str
    dialect: str
    image: ImageRecipe
    default_recipe: str
    key_env: str
    model_env: str
    recipe: str = ""

    def selected_recipe(self) -> str:
        """Return the agent definition a run loads, relative to the team (``agent.recipe``, else the default)."""
        return (self.recipe or self.default_recipe).lstrip("/")

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
        """Return why an ``agent.command`` override may not be used with this runtime, or ``""``.

        Default (goose): any override, except one naming the team mount, which would hand the CLI the team's
        own definition instead of the checked copy that ``{recipe}`` names (R7).
        """
        if any(_names_team(str(part)) for part in command):
            return (f"agent.command must not name {TEAM_IN_CONTAINER}: use {{recipe}}, the checked copy of the "
                    f"agent definition (R7)")
        return ""

    def settings_problem(self, a: dict[str, Any]) -> str:
        """Return why the ``agent.*`` settings cannot run with this runtime, or ``""`` (default: none).

        Called by :func:`validate_settings` as the config is loaded (codex: the residual-tool opt-in, D4).
        """
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


#: The in-container python the MCP server runs with (absolute: no PATH lookup).
PYTHON = "/usr/local/bin/python"
#: The vulnscan MCP server's env, as the container sets it (``agent_run._base_argv``), restated so the
#: server sees it whether or not the CLI passes its own env to stdio servers.
MCP_ENV = {"VULNSCAN_RUN_DIR": "/work/run", "VULNSCAN_SPOOL": "/work/spool/requests",
           "VULNSCAN_RESULTS": "/work/spool/results"}


def fill_command(command: list[str], *, recipe: str, session: str, task: str, run_id: str) -> list[str]:
    """Fill an ``agent.command`` override's ``{recipe}``, ``{session}``, ``{run_id}`` and ``{task}`` placeholders.

    Shared by a run (:func:`agentteams.vulnscan.agent_run.agent_argv`) and the drivers' self-test probes, so a
    passing self-test vouches for exactly the command a run uses.

    Args:
        command: The override.
        recipe: The agent definition's in-container path (the claude probe passes ``"{recipe}"`` to keep it).
        session: Session name.
        task: The task prompt.
        run_id: Run id.

    Returns:
        The filled argv.
    """
    return [part.replace("{recipe}", recipe).replace("{session}", session).replace("{run_id}", run_id)
            .replace("{task}", task) for part in command]


def json_or_none(text: str) -> object:
    """Decode one JSON value, or None for a line that is not JSON (transcript and event-stream parsing)."""
    try:
        return json.loads(text)
    except ValueError:
        return None
