"""The remediation agent's settings: defaults, the selected driver and validation (split from agent_run).

``agent_run`` re-exports every name here, so ``agent_run.DEFAULT_AGENT``, ``agent_run.validate_agent`` and
the rest keep working. Nothing here starts a process or touches Docker.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from agentteams.vulnscan import agent_drivers
from agentteams.vulnscan.agent_drivers import Driver

DEFAULT_AGENT: dict[str, Any] = {
    "enabled": False,
    "framework": "goose",                 # selects the runtime driver (agent_drivers.get_driver)
    "team_dir": "",                       # workspace whose generated team runs the job
    "recipe": "",                         # "": the driver's agent definition (goose: .goose/recipes/vuln-remediation.yaml)
    "model": "z-ai/glm-5.3",
    "upstream": "http://host.docker.internal:8791",   # the operator's route proxy; or https://openrouter.ai
    "key_file": "",                       # file containing OPENROUTER_API_KEY=... (mounted into the GATEWAY only)
    "budget_usd": 2.0,
    "max_tokens_total": 10_000_000,       # gateway token backstop for a run (integer 1..100 000 000)
    "max_unmetered_calls": 20,            # replies without usage/cost before the gateway refuses (integer >= 1)
    "allow_unmetered": False,             # true: never refuse for unmetered replies (still counted)
    "max_minutes": 45,
    "image": "",                          # default agentteams-vulnscan-agent:<agentteams version> (goose)
    "command": [],                        # []: the driver's command; advanced override with {recipe} {session} {task} {run_id}
    "dlp_skip_ok": [],                    # [{"path", "sha256"}]: secrets files the DLP check may skip (R5; that exact version)
}


def agent_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return the agent block merged over defaults.

    Args:
        cfg: Daily config.

    Returns:
        The effective agent settings.
    """
    return {**DEFAULT_AGENT, **(cfg.get("agent") or {})}


def driver_of(a: dict[str, Any]) -> Driver:
    """Return the runtime driver ``agent.framework`` selects (ValueError: unknown, or not in this release)."""
    return agent_drivers.driver_for(a, DEFAULT_AGENT["framework"])   # copilot: dialect; codex: probe model


MAX_TOKENS_TOTAL_CEILING = 100_000_000
MAX_MINUTES_CEILING = 24 * 60         # a daily run (or a debrief) never needs more than a day
BUDGET_USD_CEILING = 1000.0           # far above any sane run; a typo like 20000 is refused


def positive_number(value: object, name: str, ceiling: float) -> float:
    """Return ``value`` as a float when it is a real number in ``(0, ceiling]`` (G10, X11).

    Bools, strings and None are refused; NaN and infinity fail the range comparison.

    Args:
        value: The configured value.
        name: The setting's name, for the message (e.g. ``agent.max_minutes``).
        ceiling: The largest value allowed.

    Returns:
        The value as a float.

    Raises:
        ValueError: ``value`` is not a number in range.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= ceiling:
        raise ValueError(f"{name} must be a number greater than 0 and at most {ceiling:g} (got {value!r})")
    return float(value)


def _positive_int(a: dict[str, Any], key: str, ceiling: int | None = None) -> int:
    """Return ``a[key]`` when it is an integer in ``1..ceiling`` (bools, floats and strings are refused)."""
    v = a.get(key, DEFAULT_AGENT[key])
    if isinstance(v, bool) or not isinstance(v, int) or v < 1 or (ceiling is not None and v > ceiling):
        bound = f"1..{ceiling}" if ceiling else ">= 1"
        raise ValueError(f"agent.{key} must be an integer {bound} (got {v!r})")
    return v


def run_limits(a: dict[str, Any]) -> tuple[int, int, bool]:
    """Return the run's gateway backstops, validated (P8).

    Args:
        a: Agent settings.

    ``agent.max_minutes`` and ``agent.budget_usd`` are checked too (:func:`positive_number`, G10)
    although they are not returned.

    Returns:
        ``(max_tokens_total, max_unmetered_calls, allow_unmetered)``.

    Raises:
        ValueError: A value is missing its type or range (the run then refuses as ``not-run``).
    """
    allow = a.get("allow_unmetered", DEFAULT_AGENT["allow_unmetered"])
    if not isinstance(allow, bool):
        raise ValueError(f"agent.allow_unmetered must be true or false (got {allow!r})")
    positive_number(a.get("max_minutes", DEFAULT_AGENT["max_minutes"]), "agent.max_minutes", MAX_MINUTES_CEILING)
    positive_number(a.get("budget_usd", DEFAULT_AGENT["budget_usd"]), "agent.budget_usd", BUDGET_USD_CEILING)
    return (_positive_int(a, "max_tokens_total", MAX_TOKENS_TOTAL_CEILING),
            _positive_int(a, "max_unmetered_calls"), allow)


def validate_agent(a: dict[str, Any]) -> None:
    """Check every ``agent.*`` value's type and range, as the config is loaded (item 9).

    Numbers follow :func:`run_limits` and the debrief rules (:func:`debrief.debrief_limits`,
    :func:`debrief.debrief_max_tokens`). ``team_dir``, ``key_file`` and ``image`` must be strings
    (possibly empty); ``model`` and ``framework`` non-empty strings, the framework one with a driver in
    this release (:func:`agent_drivers.get_driver`); ``recipe`` a string (empty: the driver's); ``upstream``
    an ``http(s)://host`` URL; ``command`` a list of strings (empty: the driver's). Paths are not checked
    against the filesystem here (:func:`team_dir_problem` runs before every mount).

    Args:
        a: The ``agent`` block as written (keys absent from it take their defaults).

    Raises:
        ValueError: A value has the wrong type or range; the message names ``agent.<key>``.
    """
    from agentteams.vulnscan import debrief, gateway_context  # lazy: debrief imports this module

    merged = {**DEFAULT_AGENT, **a}
    run_limits(merged)
    debrief.debrief_limits(merged)
    debrief.debrief_max_tokens(merged)
    for key in ("team_dir", "key_file", "image"):
        if not isinstance(merged[key], str):
            raise ValueError(f"agent.{key} must be a string (got {repr(merged[key])[:80]})")
    for key in ("model", "framework"):
        if not isinstance(merged[key], str) or not merged[key].strip():
            raise ValueError(f"agent.{key} must be a non-empty string (got {repr(merged[key])[:80]})")
    # an unknown framework or one without a driver; the dialect's keys and model rule, the driver's own rule
    agent_drivers.validate_settings(merged, driver_of(merged))
    if not isinstance(merged["recipe"], str):
        raise ValueError(f"agent.recipe must be a string (got {repr(merged['recipe'])[:80]})")
    up = merged["upstream"]
    parts = urllib.parse.urlsplit(up) if isinstance(up, str) else None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"agent.upstream must be an http:// or https:// URL with a host (got {repr(up)[:80]})")
    cmd = merged["command"]
    if not isinstance(cmd, list) or not all(isinstance(c, str) for c in cmd):
        raise ValueError(f"agent.command must be a list of strings (got {repr(cmd)[:80]})")
    problem = driver_of(merged).command_problem(cmd) if cmd else ""   # claude, copilot, codex: no override at all
    if problem:
        raise ValueError(problem)
    gateway_context.validate_skip_ok(merged["dlp_skip_ok"])   # R5: a list of {path, sha256}
