# `governance_log_repair` — AgentTeamsModule

Repairs malformed rows in the append-only governance logs. It previews the fix, applies it only when the
operator approves that exact preview, and writes a backup first.

> *Source: `agentteams/governance_log_repair.py`*

---

Since the security gate refuses every decision in a malformed `security-decisions.log.csv`, a row with the wrong
number of fields has to be repaired. The logs are append-only, so a row can't be fixed by appending another.
This module fixes such rows under three rules the operator set on 2026-11-11.

- **It never guesses.** It proposes a fix for one shape only: an unquoted comma in free text, which splits one
  cell into two, the second fragment starting with a space. A record with *k* extra fields, where exactly *k*
  fields after the first start with a space, has each such fragment re-joined to the cell before it with `,`.
  Any other malformed record gets no proposal and is listed for a hand fix.
- **Nothing is written without approval.** `repair LOG` only previews: each record, the proposed row cell by
  cell under its header, and a digest of the whole proposal. `repair LOG --apply DIGEST` writes only when the
  recomputed digest equals the approved one. That binds the approval to the exact preview and to the file as
  it was then.
- **Everything else stays byte-identical.**
  - A backup `LOG.bak-<UTC timestamp>` is written first.
  - Only the malformed records' line spans are replaced, using the log's own line ending.
  - The result is re-checked with [`governance_logs.check_log`](governance-logs.md), and the backup is restored
    if the result is still malformed.

**Refused:**
- a log with a `prev_digest` column (an edit would break the hash chain);
- a malformed record that carries a `signature` (an edit would void it).

## Public surface

| Name | Purpose |
|---|---|
| `plan_repair(path) -> RepairPlan` | Preview; writes nothing. |
| `render_plan(plan) -> str` | The operator-facing preview, ending with the `--apply` command when the plan is fixable. |
| `apply_repair(path, digest) -> Path` | Apply exactly the previewed repair and return the backup path. Raises `ValueError` on refusal, digest mismatch, or a still-malformed result (after restoring the backup). |
| `RepairPlan`, `RepairItem` | The preview: header, malformed records with proposed cells or a reason, refusal and digest. |

## CLI

```
python -m agentteams.governance_logs repair LOG                  # preview; exit 0 when fixable or clean, 1 otherwise
python -m agentteams.governance_logs repair LOG --apply DIGEST   # apply that preview; backup written first
```
