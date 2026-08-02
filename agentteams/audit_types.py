"""audit_types.py — the finding record shared by every audit check module.

``AuditFinding`` used to live in ``audit``. When the agent-contract checks were carved into
``audit_agent_contract`` (CH-07), the carved module needed the type while ``audit`` needed the
carved functions — a cycle. Homing the record here breaks it without either module importing the
other's behaviour. ``audit`` re-exports ``AuditFinding``, so every existing
``from agentteams.audit import AuditFinding`` keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AuditFinding:
    """A single audit finding."""

    category: str   # "CONFLICT" | "PRESUPPOSITION" | "WARNING"
    code: str       # Short machine-readable code
    severity: str   # "error" | "warning" | "info"
    file: str       # Relative path or "(team)" for team-level findings
    description: str
