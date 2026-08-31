"""audit_types.py — the finding record shared by every audit check module.

``AuditFinding`` used to live in ``audit``. When the agent-contract checks were carved into
``audit_agent_contract`` (CH-07), the carved module needed the type while ``audit`` needed the
carved functions — a cycle. Homing the record here breaks it without either module importing the
other's behaviour. ``audit`` re-exports ``AuditFinding``, so every existing
``from agentteams.audit import AuditFinding`` keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Which files are agent files
#
# Eleven sites in `audit` and three more in `audit_agent_contract` hardcoded `.agent.md`,
# which only copilot-vscode emits. Both modules need the answer and `audit` imports
# `audit_agent_contract`, so the predicate lives here for the same reason `AuditFinding` does.
# ---------------------------------------------------------------------------

#: Files that carry the agent extension but are not agents.
_NON_AGENT_FILES: frozenset[str] = frozenset({"SETUP-REQUIRED.md"})


def _agent_file_ext(manifest: dict[str, Any]) -> str:
    """The agent-file extension for the framework this manifest was built for.

    Falls back to ``.agent.md`` only when ``framework`` is absent, which
    :func:`analyze.build_manifest` never allows — every real manifest records it, and
    ``test_build_manifest_always_records_the_framework`` holds that closed. The fallback
    exists for hand-built manifests in tests, not as a guess about production.
    """
    from agentteams.frameworks.registry import FRAMEWORKS

    adapter_cls = FRAMEWORKS.get(str(manifest.get("framework", "")))
    if adapter_cls is None:
        return ".agent.md"
    return adapter_cls().get_file_extension("agent")


def _adapter_for(manifest: dict[str, Any]):
    """The framework adapter this manifest was built for, or None for a hand-built manifest.

    The adapter is the only thing that knows its own contract — file extension, header keys,
    whether handoffs survive rendering. The audit asked none of it and assumed copilot-vscode.
    """
    from agentteams.frameworks.registry import FRAMEWORKS

    adapter_cls = FRAMEWORKS.get(str(manifest.get("framework", "")))
    return adapter_cls() if adapter_cls else None


def _is_agent_file(rel_path: str, agent_ext: str) -> bool:
    """Is *rel_path* one of this team's agent files?

    Under ``.agent.md`` the suffix alone identified an agent. Under a bare ``.md`` it does
    not — reference docs and build artifacts share it — so the two exclusions every call site
    already applied by hand are folded in here instead of repeated fourteen times.
    """
    return (
        rel_path.endswith(agent_ext)
        and "references/" not in rel_path
        and Path(rel_path).name not in _NON_AGENT_FILES
    )


def _agent_slug(rel_path: str, agent_ext: str) -> str:
    """``navigator.agent.md`` and ``navigator.md`` both give ``navigator``."""
    return Path(rel_path).name[: -len(agent_ext)]


def _agent_file_count(file_map: dict[str, str], agent_ext: str) -> int:
    """How many entries the audit classifies as agent files.

    Used by tests to catch a regression to the state where every per-file check silently
    inspected nothing and reported a clean result.
    """
    return sum(1 for p in file_map if _is_agent_file(p, agent_ext))


@dataclass
class AuditFinding:
    """A single audit finding."""

    category: str   # "CONFLICT" | "PRESUPPOSITION" | "WARNING"
    code: str       # Short machine-readable code
    severity: str   # "error" | "warning" | "info"
    file: str       # Relative path or "(team)" for team-level findings
    description: str
