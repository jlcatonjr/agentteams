"""Where vulnscan keeps the user's configuration and results. Always outside repositories.

| Item | Override | Default |
|---|---|---|
| Config dir | ``AGENTTEAMS_VULNSCAN_CONFIG_DIR`` | ``${XDG_CONFIG_HOME:-~/.config}/agentteams`` |
| Config file | — | ``<config dir>/vulnscan.json`` |
| Server kit | ``AGENTTEAMS_SERVER_KIT_DIR`` | ``<config dir>/server-kit`` (scripts and the user's ``hosts.conf`` / ``apps.conf``) |
| State dir | ``AGENTTEAMS_VULNSCAN_STATE_DIR`` | ``${XDG_STATE_HOME:-~/.local/state}/agentteams/vulnscan`` |

The state dir holds dated run folders, ``heartbeat.json`` and the scheduler log.
"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    val = os.environ.get(var, "").strip()
    return Path(val).expanduser() if val else Path.home() / fallback


def config_dir() -> Path:
    """Return the vulnscan config directory.

    Returns:
        Path (not created).
    """
    env = os.environ.get("AGENTTEAMS_VULNSCAN_CONFIG_DIR", "").strip()
    return Path(env).expanduser() if env else _xdg("XDG_CONFIG_HOME", ".config") / "agentteams"


def config_file() -> Path:
    """Return the path of ``vulnscan.json``.

    Returns:
        Path (may not exist).
    """
    return config_dir() / "vulnscan.json"


def server_kit_dir() -> Path:
    """Return where the server kit and its host/app config live.

    Returns:
        Path (not created).
    """
    env = os.environ.get("AGENTTEAMS_SERVER_KIT_DIR", "").strip()
    return Path(env).expanduser() if env else config_dir() / "server-kit"


def state_dir() -> Path:
    """Return the results directory.

    Returns:
        Path (not created).
    """
    env = os.environ.get("AGENTTEAMS_VULNSCAN_STATE_DIR", "").strip()
    return Path(env).expanduser() if env else _xdg("XDG_STATE_HOME", ".local/state") / "agentteams" / "vulnscan"


def run_dirs(state: Path | None = None) -> list[Path]:
    """Return the completed run directories (those with a ``summary.json``), oldest first.

    Args:
        state: State directory (default: :func:`state_dir`).

    Returns:
        Run directories sorted by name (their date).
    """
    state = state_dir() if state is None else state
    if not state.is_dir():
        return []
    return sorted(p for p in state.iterdir() if p.is_dir() and (p / "summary.json").exists())
