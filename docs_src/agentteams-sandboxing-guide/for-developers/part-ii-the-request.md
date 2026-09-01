# Part II — The request  (SB4–SB6)

<!-- skeleton:SB4 SB5 SB6 -->

**The knobs, in your brief** (`schemas/project-description.schema.json`):

```json
{ "privilege_profile": "confined",
  "workspace_write_roots": ["."],
  "protected_read_paths": ["/scratch/agent-b"] }
```

- **`privilege_profile`** — `cooperative` (default, no boundary) · `confined` (write-confinement) ·
  `exclusive` (adds read-exclusion of credentials + `protected_read_paths`). A **typo fails closed** at
  parse — it never silently downgrades to `cooperative`.
- **`workspace_write_roots`** — where the agent may write (default `["."]`, the whole generated tree).
- **`protected_read_paths`** — extra paths to deny reading under `exclusive` (sibling workspaces).

You may instead pass a token directly (`--target-host-features claude:sandbox`); a confined profile
expands to the framework's token automatically (goose→`goose:sandbox`, else `claude:sandbox`). Either
source counts as "confinement requested," including on `--convert-from`/`--fleet`.

> **Cost:** none to *request*; the cost is at **wiring** (Part V) — a confined team you never wire is
> just a cooperative team with extra files.

*Full detail:* [Reference Part II](../reference/part-ii-the-request.md).
