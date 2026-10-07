"""write_policy_warning.py — warn at render time when adopted agents escape ``write_policy`` (2026-10-07).

Under ``write_policy: "orchestrator-only"`` the generator narrows the agents it renders, but an adopted
(bespoke) agent file is the project's own, so it is left as it is (``write_policy.apply`` runs on rendered
content only). Before this, only the ``AR_WRITE_POLICY`` audit flagged such an agent, so a render looked
successful: mathAgents' first P5 render was reverted at a stop-condition for exactly that. This runs the same
check on the agent files this render did not write, and prints a named warning. It changes no file and no exit
code (operator decision: warn, never rewrite files agentteams did not generate).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentteams.audit import _load_files_from_disk
from agentteams.audit_agent_contract import _check_write_policy
from agentteams.audit_types import _agent_file_ext


def unnarrowed_adopted_agents(manifest: dict[str, Any], output_dir: Path, rendered: list[str]) -> list[str]:
    """Agent files agentteams does not generate that fail ``AR_WRITE_POLICY`` (errors only).

    Args:
        manifest: The team manifest.
        output_dir: The team (agents) directory.
        rendered: Team-relative paths of every file this render generates, written or not (a skipped or
            shrink-blocked file is still agentteams', and the fix for it is ``--merge``/``--overwrite``).

    Returns:
        The failing files' team-relative paths, sorted; empty when ``write_policy`` is off.

    Raises:
        Nothing: an unreadable team directory yields no warning (the audit reports it).
    """
    if manifest.get("write_policy") != "orchestrator-only" or manifest.get("framework") == "agents-md":
        return []  # agents-md declares no per-agent tools; the audit reports that once, not as an adopted agent
    agent_ext = _agent_file_ext(manifest)
    unreadable: list[str] = []
    try:
        file_map = _load_files_from_disk(output_dir, agent_ext=agent_ext, unreadable=unreadable)
    except OSError:
        return []
    ours = {Path(rel).as_posix() for rel in rendered}
    findings = _check_write_policy(file_map, agent_ext=agent_ext, framework=str(manifest.get("framework", "")),
                                   enabled=True, unreadable=unreadable)
    return sorted({f.file for f in findings if f.severity == "error" and f.file not in ours})


def warn_unnarrowed_adopted(manifest: dict[str, Any], output_dir: Path, rendered: list[str]) -> None:
    """Print a warning naming each adopted agent that ``write_policy`` does not cover.

    Args:
        manifest: The team manifest.
        output_dir: The team (agents) directory.
        rendered: Team-relative paths of every file this render generates.

    Returns:
        None.

    Raises:
        Nothing.
    """
    names = unnarrowed_adopted_agents(manifest, output_dir, rendered)
    if not names:
        return
    print(f"  ⚠  [WRITE-POLICY] {len(names)} agent file(s) agentteams does not generate are not narrowed under "
          "write_policy \"orchestrator-only\": " + ", ".join(names))
    print("     agentteams does not rewrite files it did not generate. Narrow them yourself (e.g. apply "
          "agentteams.write_policy.apply to each), or AR_WRITE_POLICY will fail the audit.")
