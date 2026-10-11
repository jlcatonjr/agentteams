# `fence_twin_acks` — AgentTeamsModule

Reads the operator's acknowledgements of kept pre-fencing twins. A twin that
[`fence_twins`](fence-twins.md) cannot prove is template text is kept, and `fleet` reports it as
REVIEW on every run. When the operator decides to keep such a twin, a row in this ledger stops that
REVIEW, and only that.

> *Source: `agentteams/fence_twin_acks.py`* — integrity-pinned.

## The ledger

`<output dir>/references/fence-twin-acks.csv`, committed with the team:

```csv
file,heading,digest,reason
agents/technical-validator.agent.md,## Accuracy Rules,3f1c0e9a2b7d4c51,CH-08..10 are project rules the template never carried; kept until moved to notes.
```

| Column | Value |
|---|---|
| `file` | The path relative to the output dir, as the merge notice prints it. |
| `heading` | The twin's heading line. |
| `digest` | `fence_twins.ack_digest(file, heading, twin)`: 16 lowercase hex characters. The kept-twin notice prints the whole row, with a placeholder for the reason. |
| `reason` | Why the twin stays. At least 40 characters after whitespace is collapsed, the same bar as `redteam.registry.MIN_REASON_CHARS`. |

A matching row changes the merge notice from `twin kept (not provably template)` to
`twin kept (acknowledged)`, with the reason. `fleet` does not count that notice as REVIEW. It lists it
in the target's `twins_acknowledged`, so the acknowledgement stays visible in the fleet report. The first
applied fleet run that shows a given acknowledgement still reports REVIEW (below).

- **It never deletes.** The row is read only after the drop decision, and only to pick the notice
  for a twin already being kept. A twin that is provably template text is dropped whether or not a
  row names it.
- **It binds to the exact twin.** An edit to the twin's text, or a change of file or heading,
  changes the digest. The row then stops applying, and the kept notice returns, saying that the
  acknowledgement on record no longer matches. Whitespace changes do not count as edits.
- **Invalid rows are skipped.** A wrong header, a malformed digest, an absolute path, a line that is
  not a heading, or a reason under 40 characters prints a warning and leaves the twin under REVIEW.
- **Unused rows are reported.** A row naming a file that the run does not render prints a warning.
- **Trust model.** The digest is unkeyed, and the ledger is team content, so anyone who can write
  the team's files can add a row. That is no wider than what such a writer can already do: unfenced
  text that is not a twin is never reviewed, and deleting the twin also clears its REVIEW. The
  ledger adds a committed, reasoned record, and `fleet` keeps listing the twin.
- **Changes are flagged for operator review.** Because no row is tied to the operator, `fleet` reports an
  acknowledged twin as REVIEW until an earlier applied fleet run has shown that exact acknowledgement
  (see [fleet: fence-twin acknowledgements are reviewed once](fleet.md#fence-twin-acknowledgements-are-reviewed-once)).
  A row added or changed by anyone, committed or not, is flagged once and cannot clear REVIEW in the run
  that first applies it.
- A twin whose span cannot be bounded (for example, a heading that appears twice unfenced) cannot be
  acknowledged; resolve it by hand.

| Name | Purpose |
|---|---|
| `LEDGER_REL` | `references/fence-twin-acks.csv`, relative to the output dir. |
| `COLUMNS` | The required header: `file`, `heading`, `digest`, `reason`. |
| `MIN_REASON_CHARS` | 40. |
| `DIGEST_HEX` | 16. |
| `TwinAck(file, heading, digest, reason)` | One validated row. |

### `load(output_dir)`

Reads the ledger under `output_dir`. Returns `{file: (TwinAck, ...)}`, which is empty when there is
no output dir or no ledger. `cli.render_pipeline.twin_sources` calls it once per run and attaches each
file's rows to its `TwinSource`.

### `parse(text, *, source='references/fence-twin-acks.csv')`

Validates ledger text and groups the rows by file. Each skipped row prints a warning to stderr.

### `ledger_row(file, heading, digest)`

Returns the CSV row to record for a twin, with a placeholder where the reason goes.
