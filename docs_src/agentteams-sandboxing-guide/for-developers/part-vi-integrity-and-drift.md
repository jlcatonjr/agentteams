# Part VI — Integrity & drift  (SB18–SB19)

<!-- skeleton:SB18 SB19 -->

The sandbox emitters **and** the launcher asset are sha256-pinned in
`references/enforcement-integrity.json` — so an accidental edit to a boundary (e.g. dropping
`--unshare-net`) trips `--verify-integrity`:

```bash
agentteams --verify-integrity          # OK, or lists the drifted module
agentteams --write-integrity-manifest  # regenerate — ONLY after an intended control change; the diff is the control
```

If you consume the launcher in another repo, keep a **byte-identical** copy and pin its sha256; a byte
change is coordinated (re-pin + re-run your deny test). The launcher header is consumer-neutral —
override the default `--netns` explicitly rather than editing the file.

*Full detail:* [Reference Part VI](../reference/part-vi-integrity-and-drift.md).
