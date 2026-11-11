"""Default ``model:`` values for generated Claude Code agent files.

Split from ``claude.py`` (CH-07 line ceiling). ``claude.py`` re-exports the constants and calls
:func:`resolve_claude_model` from ``ClaudeAdapter.render_agent_file``.
"""

from __future__ import annotations

import re

from agentteams.yaml_frontmatter import parse_yaml_front_matter as _parse_yaml_front_matter

#: Default Claude Code ``model:`` for the orchestrator slugs (``write_policy.ORCHESTRATOR_SLUGS``).
#: Pinned to Fable 5.2 (operator choice, 2026-11-11): the model picker lists Fable 5.2 and 5.1 side by
#: side, so the bare ``fable`` alias is not guaranteed to select 5.2. Revisit when a newer Fable ships.
CLAUDE_ORCHESTRATOR_MODEL_DEFAULT = "claude-fable-5-2"

#: Default Claude Code ``model:`` for every other agent. Pinning it, rather than letting
#: subagents inherit, keeps a Fable session from spawning Fable subagents (operator rule, 2026-11-11).
CLAUDE_AGENT_MODEL_DEFAULT = "opus"

# A value Claude Code accepts as ``model:``: an alias (``opus``, ``inherit``), a model id
# (``claude-opus-5-5``), optionally with a context suffix (``opus[1m]``). Copilot's display names
# (``Claude Opus 4.8 (copilot)``) and list form (``["…"]``) do not match.
_CLAUDE_MODEL_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[1m\])?$")


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
    if model and _CLAUDE_MODEL_VALUE_RE.match(model):
        return model
    yaml_body, _ = _parse_yaml_front_matter(content)
    if model is None and (yaml_body is None or not re.search(r"^model\s*:", yaml_body, re.MULTILINE)):
        return None
    from agentteams.write_policy import ORCHESTRATOR_SLUGS

    if agent_slug in ORCHESTRATOR_SLUGS:
        return CLAUDE_ORCHESTRATOR_MODEL_DEFAULT
    return CLAUDE_AGENT_MODEL_DEFAULT
