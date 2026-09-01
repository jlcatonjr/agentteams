# Part VII — Honest ceilings & red-team  (SB20–SB21)

<!-- skeleton:SB20 SB21 -->

## SB20 — What is verified, and what is not  ✅/⚙

Three distinct mechanisms carry three distinct verdicts — **never conflate them**:

1. **Linux launcher: enforcement-VERIFIED.** A live-kernel deny test proves write-outside-`--scratch`,
   credential/sibling read, and raw egress are all denied for a real process (including a real `goose`
   process), reproduced on stock `bwrap`.
2. **macOS Seatbelt: enforcement- and profile-syntax-UNVERIFIED** off a mac host (ceiling #3).
3. **Claude native Linux product arm: unverified** on stock Ubuntu (nested-userns); the *mechanism* is
   verified.

> **Reconciliation note (2026-09-01).** The sibling [Security Guide's](../../agentteams-security-guide/README.md)
> Part VI predates the framework-neutral Linux launcher and still frames confinement as *"verified on
> macOS only."* Per current source (`host_features.py`, the `confine-run.sh` status header) that framing
> is **stale**: the **Linux launcher is the enforcement-verified path** and macOS Seatbelt is the
> **unverified** one. This guide states the current facts; the security-guide skeleton, its editions, and
> its `audience-profiles.md` ceiling #4 are pending a matching correction (tracked as a remediation
> item). Treat *this* guide as current on the verification verdict.

*Source:* `agentteams/templates/universal/sandbox/confine-run.sh` (status header);
`agentteams/host_features.py` (Linux VERIFIED comment);
`docs_src/api-reference/workspace-privilege-scoping.md` (Linux verification verdict).

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
