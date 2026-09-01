# Part VII — Ceilings & red-team  (SB20–SB21)

<!-- skeleton:SB20 SB21 -->

The honest verdicts a reviewer should record:

- **Verified (ceiling #3):** the **Linux bwrap launcher** — a live-kernel deny test proves
  write-outside-scratch, credential/sibling read, and raw egress are all denied for a real process
  (incl. a real `goose`). **Unverified:** macOS Seatbelt (off a mac) and claude's native Linux arm (stock
  Ubuntu nested-userns). These are three distinct mechanisms with three distinct verdicts.
  *(The sibling security guide's "verified on macOS only" is stale and pending correction — this guide is
  current.)*
- **Not closed (ceiling #4):** **T6 / host-as-TCB** — a same-host operator shell or key-holding peer is
  out of scope; the sandbox boxes a mis-steered *agent*, not a determined local principal.
  **seccomp/Landlock is a further layer not yet added** (the launcher is filesystem + netns + NoNewPrivs,
  not syscall filtering).

The red-team methodology behind the "VERIFIED" claim is an escape/deny test on a live kernel — a boundary
is only ever described as **"engages as tested."**

*Detail:* [Reference Part VII](../reference/part-vii-ceilings-and-red-team.md).
