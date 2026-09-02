# Part VII — Ceilings & red-team  (SB20–SB21)

<!-- skeleton:SB20 SB21 -->

**Know what you're buying:**

- **Ceiling #3 — verified only on Linux (so far).** The launcher's **Linux `bwrap`** branch is live-kernel deny-tested (write-outside-
  scratch, credential read, raw egress all denied). The newer **macOS `build_macos`** branch ships `mac-escape-tests.sh` but is **enforcement-UNVERIFIED** until that passes unnested on a real mac (memory UNCAPPED, no syscall filtering there). The native **macOS Seatbelt is UNVERIFIED** off a mac; claude's
  native Linux arm is unverified on stock Ubuntu. (The sibling security guide's earlier "macOS only
  verified" wording has since been CORRECTED to match — the two guides now agree; see
  [Reference Part VII](../reference/part-vii-ceilings-and-red-team.md).)
- **Ceiling #4 — closes nothing absolutely.** A same-host operator/key-holder (T6) and host-as-TCB stay
  open; **seccomp/Landlock is not yet added**. The hook's uncovered surfaces are your responsibility.

Treat every boundary as **"engages as tested,"** never "secure."

*Full detail:* [Reference Part VII](../reference/part-vii-ceilings-and-red-team.md).
