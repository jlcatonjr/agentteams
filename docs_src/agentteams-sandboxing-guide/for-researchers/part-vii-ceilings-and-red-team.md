# Part VII — Ceilings & red-team  (SB20–SB21)

<!-- skeleton:SB20 SB21 -->

The honest verdicts a reviewer should record:

- **Verified (ceiling #3):** the launcher's **Linux `bwrap`** branch — a live-kernel deny test proves
  write-outside-scratch, credential/sibling read, and raw egress are all denied for a real process
  (incl. a real `goose`). **Unverified:** the launcher's newer **macOS `build_macos`** branch (ships
  `mac-escape-tests.sh` but not yet passed on a mac; mem UNCAPPED, no syscall filtering), the native
  macOS Seatbelt paths (off a mac), and claude's native Linux product arm (stock Ubuntu nested-userns).
  Distinct mechanisms, distinct verdicts.
  *(The sibling security guide's earlier "verified on macOS only" wording has since been CORRECTED to
  match — the two guides now agree.)*
- **Not closed (ceiling #4):** **T6 / host-as-TCB** — a same-host operator shell or key-holding peer is
  out of scope; the sandbox boxes a mis-steered *agent*, not a determined local principal.
  **seccomp/Landlock is a further layer not yet added** (the launcher is filesystem + netns + NoNewPrivs,
  not syscall filtering).

The red-team methodology behind the "VERIFIED" claim is an escape/deny test on a live kernel — a boundary
is only ever described as **"engages as tested."**

*Detail:* [Reference Part VII](../reference/part-vii-ceilings-and-red-team.md).
