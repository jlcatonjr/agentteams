"""Per-framework paths of the files an agent team's prose names: instructions file, agents directory, agent suffix.

Templates are authored with Copilot's layout in mind. A path the prose gives to an agent must be the one the
team's own framework emits, or a Claude, Goose or Codex orchestrator sends its agents to files their project does
not have (#609). Each value is read from the adapter (``get_agents_dir`` and ``finalize_output_path``), the same
way ``backup`` and ``registry.conflict_log_path_for`` resolve them, so no second framework → path table exists to
drift (CH-05).
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from agentteams.frameworks.format_spec import COPILOT_INSTRUCTIONS_FILENAME
from agentteams.frameworks.registry import FRAMEWORKS, conflict_log_path_for

def canonical_path_placeholders(framework: str) -> dict[str, str]:
    """Return the auto-resolved path placeholders for a framework's team.

    Args:
        framework: A registered framework id.

    Returns:
        ``TEAM_INSTRUCTIONS_FILE`` (project-relative, e.g. ``.claude/CLAUDE.md``), ``TEAM_AGENTS_DIR``
        (e.g. ``.claude/agents``), ``AGENT_FILE_SUFFIX`` (e.g. ``.md``, ``.agent.md``, ``.toml``) and
        ``CONFLICT_LOG_PATH``.

    Raises:
        ValueError: If ``framework`` is not registered (as ``registry.agents_dir_for`` does): a silent Copilot
            fallback would send the team to the paths this module exists to replace.
    """
    if framework not in FRAMEWORKS:
        raise ValueError(f"unknown framework {framework!r}")
    adapter = FRAMEWORKS[framework]()
    agents_dir = adapter.get_agents_dir(Path(".")).as_posix()
    instructions = adapter.finalize_output_path(f"../{COPILOT_INSTRUCTIONS_FILENAME}", "instructions")
    return {
        "TEAM_INSTRUCTIONS_FILE": posixpath.normpath(posixpath.join(agents_dir, instructions)),
        "TEAM_AGENTS_DIR": agents_dir,
        "AGENT_FILE_SUFFIX": adapter.finalize_output_path("x.agent.md", "agent")[len("x"):],
        "CONFLICT_LOG_PATH": conflict_log_path_for(framework),
    }
