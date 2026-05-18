# `plan_steps` — AgentTeamsModule

Tolerant reader for plan `.steps.csv` artifacts.

Plans in `tmp/by-week/<ISO-week>/<slug>.steps.csv` describe sequenced handoffs between agents. Some cells (typically `notes`) contain quoted multi-line text. This module's `read_steps` wraps `csv.DictReader` with the conventions used across the project — quoted multi-line cells preserved, rows without a `step` field skipped, missing cells coerced to empty strings.

Introduced alongside [`handoff_payloads`](handoff_payloads.md) so the chain comparator can operate on dict rows.

> *Source: `agentteams/plan_steps.py`*

---

## Functions

### `read_steps(path)`

> *Source: `agentteams/plan_steps.py`*

Read a plan `.steps.csv`. Each returned dict is keyed by the CSV header row; missing values are normalized to empty strings (not `None`).

**Args:**

- `path` (`Path | str`) — Path to a plan `.steps.csv` artifact.

**Returns:** `list[dict[str, str]]` — One dict per data row. Rows whose `step` cell is empty are skipped (this allows trailing blank lines and comment-style header gaps without raising).

**Example:**

```python
from pathlib import Path
from agentteams.plan_steps import read_steps
from agentteams.handoff_payloads import audit_handoff_chain

steps = read_steps(Path("tmp/by-week/2026-W21/my-plan.steps.csv"))
findings = audit_handoff_chain(steps)
for finding in findings:
    print(f"{finding.severity} {finding.code}: {finding.message}")
```

---

## See Also

- [`handoff_payloads`](handoff_payloads.md) — typed handoff substrate that consumes the rows returned here.
