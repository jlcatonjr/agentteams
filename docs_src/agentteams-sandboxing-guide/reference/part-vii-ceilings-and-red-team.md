# Part VII — Honest ceilings & red-team  (SB20–SB21)

<!-- skeleton:SB20 SB21 -->

## SB20 — What is verified, and what is not  ✅/⚙

Distinct mechanisms carry distinct verdicts — **never conflate them**:

1. **Launcher Linux (`bwrap`) branch: enforcement-VERIFIED.** A live-kernel deny test proves
   write-outside-`--scratch`, credential/sibling read, and raw egress are all denied for a real process
   (including a real `goose` process), reproduced on stock `bwrap`.
2. **Launcher macOS (`build_macos`) branch: enforcement-UNVERIFIED** (2026-W36). Its intended on-host
   deny test is **`sandbox/mac-escape-tests.sh`**, which must pass **unnested, with its positive
   controls**, on a real mac before the macOS boundary may be called "confined" — *wiring-verified ≠
   enforcement-verified*. **Known gap (as shipped):** that test hard-targets a `confine-run.macos-ref.sh`
   wrapper agentteams does **not** currently emit and exits early if it is absent — so the gate is **not
   yet runnable against the emitted launcher**, and macOS cannot become "verified" until that
   test/wrapper mismatch is fixed (logged for the launcher/test owner). Honest residuals: memory UNCAPPED,
   no syscall filtering, setuid denylist ≠ NoNewPrivs, loopback-only proxy (SB12).
3. **Native macOS Seatbelt (goose/claude) + Claude's native Linux product arm: also unverified** — the
   goose/claude Seatbelt profiles are enforcement/profile-syntax-unverified off a mac; Claude Code's
   Linux bubblewrap *product arm* is unverified on stock Ubuntu (nested-userns; the mechanism is verified).

> **Cross-guide reconciliation (done, 2026-09-01).** The sibling
> [Security Guide's](../../agentteams-security-guide/README.md) earlier *"verified on macOS only"*
> framing was stale (it predated the launcher) and has since been **corrected** to match current source
> (Linux deny-tested; macOS unverified) across its skeleton, four editions, and `audience-profiles.md`,
> with a cross-link back here. The two guides now agree; the only nuance this guide adds is the newer
> macOS `build_macos` branch (verdict 2 above).

*Source:* `agentteams/templates/universal/sandbox/confine-run.sh` (status header);
`agentteams/templates/universal/sandbox/mac-escape-tests.sh` (the macOS deny test);
`agentteams/host_features.py` (Linux VERIFIED comment; macOS advisory);
`docs_src/api-reference/workspace-privilege-scoping.md` (verification verdict + macOS augmentation).

## SB21 — What sandboxing does NOT close  ✅/⚙  (ceiling #4)

1. **T6 / host-as-TCB stay bounded, never closed.** A same-host operator shell or a key-holding peer is
   out of scope for these controls; the sandbox boxes a mis-steered *agent*, not a determined local
   principal.
2. **seccomp/Landlock is a further layer NOT yet added** — the bwrap launcher is filesystem + netns +
   NoNewPrivs confinement, not syscall filtering.
3. The PreToolUse hook's uncovered surfaces (SB16) remain the operator's responsibility.

The honest posture throughout: a boundary is described as **"engages as tested,"** never "secure" or
"unbypassable."

*Source:* `agentteams/templates/universal/sandbox/confine-run.sh` (policy header);
`agentteams/templates/universal/security.template.md` (delete-gate limits).

> **Next:** [Part VIII — Synthesis & reference matter](part-viii-synthesis-and-reference.md).
