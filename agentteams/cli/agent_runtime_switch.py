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

    Raises:
        Nothing.
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
        Nothing: the merged block is validated afterwards by ``ingest.validate`` (which reports a bad flag value as a
        validation error, before anything renders).
    """
    given = [(dest, role, field, v) for dest, role, field in _FLAGS for v in (getattr(args, dest, None) or [])]
    if not given:
        return []
    block = copy.deepcopy(description.get("agent_runtime") or {})
    if not isinstance(block, dict):
        return []  # ingest.validate reports the malformed block
    notices: list[str] = []
    for dest, role, field, raw in given:
        provider, value = _split(raw)
        entry = block.setdefault(role, {})
        if not isinstance(entry, dict):
            return []  # ingest.validate reports the malformed role
        current = entry.get(field)
        if field == "model":
            # The flag wins for every surface it names: `copilot=` replaces both Copilot surfaces' entries.
            models = dict(current) if isinstance(current, dict) else ({"claude": current} if current else {})
            key = provider or "claude"
            for covered in (("copilot", "copilot-vscode", "copilot-cli") if key == "copilot" else (key,)):
                models.pop(covered, None)
            models[key] = value
            entry["model"] = models
        elif provider is None:
            entry["effort"] = value          # a bare level replaces every provider level the brief set
        else:
            efforts = dict(current) if isinstance(current, dict) else (
                {"default": current} if isinstance(current, str) else {})
            for covered in (("copilot", "copilot-vscode", "copilot-cli") if provider == "copilot" else (provider,)):
                efforts.pop(covered, None)
            efforts[provider] = value
            entry["effort"] = efforts
        notices.append(f"--{dest.replace('_', '-')} {raw}")
    description["agent_runtime"] = block
    return [f"agent runtime: set for this run only by {', '.join(notices)}; not kept. Add it to the brief's "
            "agent_runtime to keep it across --update."]


def apply_flags(description: dict[str, Any], args: argparse.Namespace) -> list[str]:
    """Apply the flags and print their one-run notice (``generate``'s one call, before ``ingest.validate``).

    Args:
        description: The loaded project brief (its ``agent_runtime`` may be replaced by a merged copy).
        args: Parsed arguments.

    Returns:
        Validation errors to add (always empty: ``ingest.validate`` checks the merged block).

    Raises:
        Nothing.
    """
    import sys

    for line in apply_to_description(description, args):
        print(f"  !  {line}", file=sys.stderr)
    return []


__all__ = ["add_agent_runtime_arguments", "apply_flags", "apply_to_description"]
