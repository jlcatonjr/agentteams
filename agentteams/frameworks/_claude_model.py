"""Default ``model:`` values for generated Claude Code agent files.

Split from ``claude.py`` (CH-07 line ceiling). ``claude.py`` re-exports the constants and calls
:func:`resolve_claude_model` from ``ClaudeAdapter.render_agent_file``.
"""

from __future__ import annotations

import json
import re
from typing import TypeGuard

from agentteams.yaml_frontmatter import parse_yaml_front_matter as _parse_yaml_front_matter

#: Default Claude Code ``model:`` for the orchestrator slugs (``write_policy.ORCHESTRATOR_SLUGS``).
#: Pinned to Fable 5.2 (operator choice, 2026-11-11): the model picker lists Fable 5.2 and 5.1 side by
#: side, so the bare ``fable`` alias is not guaranteed to select 5.2. Revisit when a newer Fable ships.
CLAUDE_ORCHESTRATOR_MODEL_DEFAULT = "claude-fable-5-2"

#: Default Claude Code ``model:`` for every other agent. Pinning it, rather than letting
#: subagents inherit, keeps a Fable session from spawning Fable subagents (operator rule, 2026-11-11).
CLAUDE_AGENT_MODEL_DEFAULT = "opus"

#: The ``env`` variable that sets the model of a subagent whose definition names none: Claude Code's built-in
#: agents (``general-purpose``, …) and team agents with no ``model:`` key. It ranks below a front-matter ``model:``,
#: so the orchestrator file keeps :data:`CLAUDE_ORCHESTRATOR_MODEL_DEFAULT`.
CLAUDE_SUBAGENT_MODEL_ENV = "CLAUDE_CODE_SUBAGENT_MODEL"

_SESSION_MODEL_COMMENT = (
    "",
    f"`model` and `env.{CLAUDE_SUBAGENT_MODEL_ENV}`: the main session runs {CLAUDE_ORCHESTRATOR_MODEL_DEFAULT} and every "
    f"subagent whose file names no model runs {CLAUDE_AGENT_MODEL_DEFAULT}, matching the agent files' own `model:` lines.",
    "settings.local.json, ANTHROPIC_MODEL, --model and /model still override the session model.",
)

# A value Claude Code accepts as ``model:``: an alias (``opus``, ``inherit``), a model id
# (``claude-opus-5-5``), optionally with a context suffix (``opus[1m]``). Copilot's display names
# (``Claude Opus 4.8 (copilot)``) and list form (``["…"]``) do not match.
_CLAUDE_MODEL_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[1m\])?$")


def is_claude_model_value(value: object) -> TypeGuard[str]:
    """Return True when ``value`` is a ``model:`` value Claude Code can read.

    Args:
        value: A scalar front-matter value; anything that is not a ``str`` is not a model value.

    Returns:
        True for an alias or model id (``opus``, ``inherit``, ``claude-fable-5-2``, ``opus[1m]``).

    Raises:
        Nothing.
    """
    return isinstance(value, str) and _CLAUDE_MODEL_VALUE_RE.match(value) is not None


def default_claude_model(agent_slug: str) -> str:
    """Return the default Claude ``model:`` for ``agent_slug``.

    Args:
        agent_slug: The agent's slug.

    Returns:
        :data:`CLAUDE_ORCHESTRATOR_MODEL_DEFAULT` for the orchestrator slugs, else
        :data:`CLAUDE_AGENT_MODEL_DEFAULT`.

    Raises:
        Nothing.
    """
    from agentteams.write_policy import ORCHESTRATOR_SLUGS

    if agent_slug in ORCHESTRATOR_SLUGS:
        return CLAUDE_ORCHESTRATOR_MODEL_DEFAULT
    return CLAUDE_AGENT_MODEL_DEFAULT


def resolve_claude_model(content: str, model: str | None, agent_slug: str) -> str | None:
    """Return the ``model:`` value to emit for a Claude agent file.

    A source ``model:`` that Claude Code can read (an alias or model id) is kept. A source that
    declares a model Claude cannot read — every template carries Copilot's
    ``model: ["Claude Opus 4.8 (copilot)"]`` — gets the Claude default instead:
    :data:`CLAUDE_ORCHESTRATOR_MODEL_DEFAULT` for the orchestrator slugs and
    :data:`CLAUDE_AGENT_MODEL_DEFAULT` for every other agent. A source with no ``model:`` key at
    all (for example a Claude team imported through CAI) keeps inheriting the session model.

    Args:
        content: The agent file content, front matter included.
        model: The scalar ``model`` value captured by :func:`agentteams.frameworks.claude._extract_claude_optional_keys`.
        agent_slug: The agent's slug.

    Returns:
        The model value to emit, or ``None`` to emit no ``model:`` key.

    Raises:
        Nothing.
    """
    if is_claude_model_value(model):
        return model
    yaml_body, _ = _parse_yaml_front_matter(content)
    if model is None and (yaml_body is None or not re.search(r"^model\s*:", yaml_body, re.MULTILINE)):
        return None
    return default_claude_model(agent_slug)


def inject_session_model(example_text: str) -> str:
    """Return the Claude settings example with the session-model keys added.

    Adds ``"model": CLAUDE_ORCHESTRATOR_MODEL_DEFAULT`` and ``"env": {CLAUDE_SUBAGENT_MODEL_ENV:
    CLAUDE_AGENT_MODEL_DEFAULT}``, keeping any value the example already sets, and a ``_comment`` note. The
    example stays inert until an operator merges it into ``settings.json``.

    Args:
        example_text: The ``settings.hooks.example.json`` text (with or without the sandbox block).

    Returns:
        The example re-serialized (keys sorted, as the sandbox injector writes it) with the keys added.

    Raises:
        TypeError: ``example_text`` is not a ``str``.
        ValueError: the example is not a JSON object, or its ``env`` is not an object. It is an agentteams-shipped,
            test-covered asset, so this is agentteams' own bug and fails loud, as ``_inject_sandbox_block`` does.
    """
    if not isinstance(example_text, str):
        raise TypeError(f"settings example must be str, not {type(example_text).__name__}")
    try:
        data = json.loads(example_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"settings.hooks.example.json is not valid JSON; cannot add the session model: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("settings.hooks.example.json did not parse to a JSON object; cannot add the session model")
    data.setdefault("model", CLAUDE_ORCHESTRATOR_MODEL_DEFAULT)
    env = data.setdefault("env", {})
    if not isinstance(env, dict):
        raise ValueError("settings.hooks.example.json `env` is not an object; cannot add the session model")
    env.setdefault(CLAUDE_SUBAGENT_MODEL_ENV, CLAUDE_AGENT_MODEL_DEFAULT)
    comment = data.setdefault("_comment", [])
    if isinstance(comment, list):
        comment.extend(_SESSION_MODEL_COMMENT)
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
