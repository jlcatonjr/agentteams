# `governance_log_repair` — AgentTeamsModule

Repairs malformed rows in the append-only governance logs. It previews the fix, applies it only when the
operator approves that exact preview, and writes a backup first.

> *Source: `agentteams/governance_log_repair.py`*

---

Since the security gate refuses every decision in a malformed `security-decisions.log.csv`, a row with the wrong
number of fields has to be repaired. The logs are append-only, so a row can't be fixed by appending another.
This module fixes such rows under three rules the operator set on 2026-11-11.

- **It never guesses, and it never touches a decision.** It proposes a fix for one shape only: an unquoted comma
  in prose, which splits one cell into two, the second fragment starting with a space. A record with *k* extra
  fields, where exactly *k* fields after the first start with a space, has each fragment re-joined to the cell
  before it with `,`. That happens only if all of these hold:
  - every re-join lands in a free-text column (`FREE_TEXT_COLUMNS`: `conditions`, `conditions_verified`,
    `evidence`, `notes`, `description`, `summary`, `resolution`, and so on);
  - every cell before the first re-join keeps its position and content;
  - no decision column (`decision`, `action_reviewed`, `status`, `verdict`, `scope`) sits after a re-join;
  - no fragment is a bare verdict word.

  Anything else gets no proposal and is listed for a hand fix. So are `check_log` problems other than field
  counts, such as an open quote or swallowed rows.
- **The operator approves it.**
  - `repair LOG` previews each record twice, cell by cell under its header: as read (by position) and as
    proposed. It ends with a digest of the whole proposal.
  - `repair LOG --apply DIGEST` writes only when the recomputed digest equals the approved one.
  - The CLI also needs an interactive terminal, where the operator retypes the digest's first 8 characters. An
    agent has no terminal, so it cannot approve its own preview.
- **Everything else stays byte-identical, and no concurrent append is lost.**
  - The log is locked and read once, and the plan is computed from exactly those bytes.
  - A backup `LOG.bak-<UTC timestamp>` is written first (exclusive create, fsync). Remove it once the log has
    been checked.
  - The log is rewritten in place through the locked descriptor, so an `append_row` waiting on the lock appends
    to the repaired file.
  - Lines are counted the way the csv module counts them.
  - The result is re-checked with [`governance_logs.check_log`](governance-logs.md), and the original bytes are
    restored on failure.
- Records are numbered the way `check_log` numbers them, blank records included.

**Refused outright:** a log with a signing, chain or digest column (`signature`, `sig_scheme`, `key_id`,
`derives_from`, `prev_digest`, `text_digest`), matched after stripping whitespace. Those need a reviewed
migration instead.

## Public surface

| Name | Purpose |
|---|---|
| `plan_repair(path) -> RepairPlan` | Preview; writes nothing. |
| `render_plan(plan) -> str` | The operator-facing preview, ending with the `--apply` command when the plan is fixable. |
| `apply_repair(path, digest) -> Path` | Apply exactly the previewed repair and return the backup path. Raises `ValueError` on refusal, digest mismatch, or a still-malformed result (after restoring the backup). |
| `RepairPlan`, `RepairItem` | The preview: header, malformed records with proposed cells or a reason, refusal and digest. |

## CLI

```
python -m agentteams.governance_logs repair LOG                  # preview; exit 0 when fixable or clean (check_log passes), 1 otherwise
python -m agentteams.governance_logs repair LOG --apply DIGEST   # operator, interactive terminal: confirm, back up, apply
```
