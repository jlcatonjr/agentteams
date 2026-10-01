"""Generation-time advisories for launcher residuals (follow-up #11, 2026-09-30).

Two states a confined process can leave behind are reported, never acted on:

* a **marker-only team dir**: ``<agents dir>/references/build-log.json`` with no agent files and no
  switch. The launcher refuses such a project (the marker makes the team's trust roots required);
  the operator should remove a planted marker from outside the sandbox. It is still denied in the
  Claude block (excluding it would let an agent drop a real team's deny by deleting agent files).
* **security-relevant keys in ``.codex/config.toml``** (``approval_policy``, ``sandbox_mode``,
  ``notify``, ``[sandbox_workspace_write]``, ``[mcp_servers.*]``): the file is protect-if-present,
  so a planted one is locked in, and Codex run outside the launcher honours it. Key-based on every
  run: a digest recorded at generation would bless whatever was planted before it.

Detection only, and best-effort: the plant heuristic is a diagnosis an attacker can steer (one
dummy agent file), and key detection sees plain ``key =`` lines and ``[table]`` headers only (not
quoted or dotted keys, inline tables, or ``model_providers`` ``base_url``). A symlinked
``config.toml`` is reported as such. Printed paths are ``repr``-escaped.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: agents dir (project-relative) -> agent-file glob of its framework
_TEAM_AGENT_GLOBS: dict[str, str] = {
    ".claude/agents": "*.md", ".goose/recipes": "*.yaml",
    ".github/agents": "*.agent.md", ".codex/agents": "*.toml",
}
_MARKER = "references/build-log.json"
_SWITCH = "references/agent-privilege.json"
_CODEX_KEY_RE = re.compile(
    r"^\s*(?:(approval_policy|sandbox_mode|notify)\s*=|\[(sandbox_workspace_write|mcp_servers)[\].])"
)


def marker_only_team_dirs(project_root: Path) -> list[str]:
    """Return the project-relative agents dirs that hold only an agentteams marker.

    Args:
        project_root: The project root.

    Returns:
        The agents dirs with a build-log marker, no switch and no agent file of their framework.
    """
    found: list[str] = []
    for rel, glob in _TEAM_AGENT_GLOBS.items():
        team = project_root / rel
        if not (team / _MARKER).is_file() or (team / _SWITCH).exists():
            continue
        agents = [p for p in team.glob(glob) if p.name != "SETUP-REQUIRED.md"]
        if not agents:
            found.append(rel)
    return found


def codex_config_security_keys(project_root: Path) -> list[str]:
    """Return the security-relevant keys set in ``<project>/.codex/config.toml`` (in file order).

    Args:
        project_root: The project root.

    Returns:
        Key names (tables as ``[name]``); empty when the file is absent or unreadable.
    """
    path = project_root / ".codex" / "config.toml"
    if path.is_symlink():  # Codex follows the link: report it, never read the target
        return ["<symlink>"]
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    keys: list[str] = []
    for line in text.splitlines():
        m = _CODEX_KEY_RE.match(line)
        if m:
            key = m.group(1) or f"[{m.group(2)}]"
            if key not in keys:
                keys.append(key)
    return keys


def print_team_dir_advisories(project_root: Path) -> None:
    """Print both advisories to stderr (no-op when nothing is found).

    Args:
        project_root: The project root.
    """
    for rel in marker_only_team_dirs(project_root):
        print(f"  WARNING: {rel!r} holds only an agentteams marker ({_MARKER}) and no agent files: "
              "it may have been PLANTED by a confined process, and it makes sandbox/confine-run.sh "
              "refuse this project. If you did not generate a team there, remove the marker from "
              "outside any sandbox.", file=sys.stderr)
    keys = codex_config_security_keys(project_root)
    if keys == ["<symlink>"]:
        print("  NOTE: .codex/config.toml is a symlink; Codex reads its target. Review the target and "
              "confirm you created the link.", file=sys.stderr)
    elif keys:
        print(f"  NOTE: .codex/config.toml sets security-relevant Codex keys ({', '.join(keys)}). "
              "Confined processes cannot change it once it exists, but could have created it: "
              "confirm you wrote these before running Codex outside the launcher.", file=sys.stderr)
