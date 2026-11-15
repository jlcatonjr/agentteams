"""agent_runtime_switch.py — the ``--orchestrator-*`` / ``--subagent-*`` model and effort flags (agentteams #508).

Each flag overrides the brief's ``agent_runtime`` for one run only. Values are not persisted: the build log is generated
data (C-4) and is never read back as a setting. The run prints which values came from flags and that they are kept
only by adding them to the brief.

Stdlib only.
"""

from __future__ import annotations

import argparse
import copy
from typing import Any

#: (flag dest, role, field)
_FLAGS: tuple[tuple[str, str, str], ...] = (
    ("orchestrator_model", "orchestrator", "model"),
    ("orchestrator_effort", "orchestrator", "effort"),
    ("subagent_model", "subagents", "model"),
    ("subagent_effort", "subagents", "effort"),
)


def add_agent_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--orchestrator-model``, ``--orchestrator-effort``, ``--subagent-model`` and ``--subagent-effort``.

    Args:
        parser: The agentteams argument parser.

    Returns:
        None.
    """
    model_help = ("[PROVIDER=]MODEL: the {role} model for this run, overriding the brief's agent_runtime.{key}.model "
                  "(PROVIDER is claude (the default), copilot, copilot-vscode, copilot-cli, codex, goose or grok; a "
                  "bare model name only). Repeat for several providers. Not kept: add it to the brief to keep it.")
    effort_help = ("LEVEL: the {role} reasoning effort for this run (low, medium, high, xhigh, max; or PROVIDER=LEVEL "
                   "for a provider's own level), overriding the brief's agent_runtime.{key}.effort. Default medium. "
                   "Not kept: add it to the brief to keep it.")
    for dest, role, field in _FLAGS:
        flag = "--" + dest.replace("_", "-")
        who = "orchestrator" if role == "orchestrator" else "subagent (every non-orchestrator agent)"
        parser.add_argument(
            flag, dest=dest, action="append", default=None,
            metavar="[PROVIDER=]MODEL" if field == "model" else "LEVEL",
            help=(model_help if field == "model" else effort_help).format(role=who, key=role),
        )


def _split(value: str) -> tuple[str | None, str]:
    if "=" in value:
        provider, _, rest = value.partition("=")
        return provider.strip(), rest.strip()
    return None, value.strip()


def apply_to_description(description: dict[str, Any], args: argparse.Namespace) -> list[str]:
    """Fold the flags into a copy of the brief's ``agent_runtime``, in place on *description*.

    Args:
        description: The loaded project brief (mutated: its ``agent_runtime`` is replaced by a merged copy).
        args: Parsed arguments.

    Returns:
        Notice lines naming each flag-set value (empty when no flag was given).

    Raises:
        agentteams.agent_runtime.AgentRuntimeError: A flag value the block validation refuses.
    """
    from agentteams import agent_runtime as _ar

    given = [(dest, role, field, v) for dest, role, field in _FLAGS for v in (getattr(args, dest, None) or [])]
    if not given:
        return []
    block = copy.deepcopy(description.get("agent_runtime") or {})
    if not isinstance(block, dict):
        raise _ar.AgentRuntimeError("agent_runtime must be an object")
    notices: list[str] = []
    for dest, role, field, raw in given:
        provider, value = _split(raw)
        entry = block.setdefault(role, {})
        if field == "model":
            current = entry.get("model")
            models = dict(current) if isinstance(current, dict) else ({"claude": current} if current else {})
            models[provider or "claude"] = value
            entry["model"] = models
        else:
            current = entry.get("effort")
            if provider is None:
                if isinstance(current, dict):
                    current = dict(current)
                    current["default"] = value
                    entry["effort"] = current
                else:
                    entry["effort"] = value
            else:
                efforts = dict(current) if isinstance(current, dict) else (
                    {"default": current} if isinstance(current, str) else {})
                efforts[provider] = value
                entry["effort"] = efforts
        notices.append(f"--{dest.replace('_', '-')} {raw}")
    description["agent_runtime"] = block
    _ar.parse(description)  # fail closed on a bad flag value before anything renders
    return [f"agent runtime: set for this run only by {', '.join(notices)}; not kept. Add it to the brief's "
            "agent_runtime to keep it across --update."]


__all__ = ["add_agent_runtime_arguments", "apply_to_description"]
