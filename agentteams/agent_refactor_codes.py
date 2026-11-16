"""Opt-in typed violation codes (AR-01..AR-10) for the agent-refactor template (#453).

Chapter 13 of the book has the agent refactorer report spec-compliance violations with ten typed
codes. The portable default (ledger row TA-009, ``not-a-gap``) keeps the template's check table
only, so this section is emitted only when the brief sets ``agent_refactor.violation_codes: true``.
With the switch off, ``{AGENT_REFACTOR_VIOLATION_CODES}`` resolves to the empty string and the
rendered agent file is byte-identical to the render without this feature.

The section carries no fence of its own. The agent-refactor template is unfenced, so the emitter
wraps the whole body in the ``content`` fence, and ``--update --merge`` replaces that fence in full:
turning the switch on or off reaches an existing team on its next update. An inner fence here would
stop that wrap and send the file through the whole-body migration path instead.
"""

from __future__ import annotations

from typing import Any

PLACEHOLDER = "AGENT_REFACTOR_VIOLATION_CODES"
DEFAULT_LINE_BUDGET = 200

_SECTION = """

### Violation Codes

Report every spec-compliance violation with one of these codes, so each finding can be reproduced and counted per code.

| Code | Violation | Check |
|------|-----------|-------|
| AR-01 | Missing required front-matter field | One of the five fields in the Part 2 front-matter row is absent (the portable baseline; a file with no `handoffs:` key is reported as AR-10) |
| AR-02 | H1 title does not match the `name` field | The first `# ` heading differs from the front-matter `name` value |
| AR-03 | Inline data block meets the extraction threshold | A block meets all four Part 1 extraction criteria (size, type, shared, volatile) but is still inline |
| AR-04 | Dead `#file:` reference | A `#file:` reference names a path that does not exist on disk |
| AR-05 | Agent file exceeds {line_budget} lines without justified extraction | The file is longer than {line_budget} lines and records no reason for keeping its content inline; the evidence gives the actual line count |
| AR-06 | Invariant Core modified | The Invariant Core section differs from the abstract template the file was generated from |
| AR-07 | Handoff to an agent outside the orchestrator's roster | A `handoffs:` entry names an agent that is not in the orchestrator's `agents:` list |
| AR-08 | Missing purpose section | The file has no statement of the agent's purpose under its H1 title |
| AR-09 | Duplicate content across agent files | The same volatile content block appears in two or more agent files instead of one reference file |
| AR-10 | No handoff back to the orchestrator | The file has no "Return to Orchestrator" handoff |

A reference file in `.github/agents/references/` that no agent file cites has no code. Report it as an orphan finding, separate from AR-04.

Report findings as one row per violation, then one count line per code with at least one finding:

| Code | File | Evidence |
|------|------|----------|
| AR-05 | `.github/agents/<slug>.agent.md` | 312 lines (budget {line_budget}) |

`Counts: AR-05 x1`"""


def _settings(source: dict[str, Any]) -> dict[str, Any]:
    value = source.get("agent_refactor")
    return value if isinstance(value, dict) else {}


def manifest_fields(description: dict[str, Any]) -> dict[str, Any]:
    """Carry ``agent_refactor`` into the manifest only when the brief turns the codes on."""
    settings = _settings(description)
    if settings.get("violation_codes") is not True:
        return {}
    budget = settings.get("line_budget", DEFAULT_LINE_BUDGET)
    # The schema rejects these too; this guard keeps a brief that skipped validation from writing
    # `True` or "200" into the agent file's text. bool is an int subclass, so test it first.
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise ValueError(f"agent_refactor.line_budget must be an integer >= 1, got {budget!r}")
    return {"agent_refactor": {"violation_codes": True, "line_budget": budget}}


def section(description: dict[str, Any]) -> str:
    """Return the AR-01..AR-10 section, or "" when the brief leaves the codes off."""
    fields = manifest_fields(description)
    if not fields:
        return ""
    return _SECTION.replace("{line_budget}", str(fields["agent_refactor"]["line_budget"]))
