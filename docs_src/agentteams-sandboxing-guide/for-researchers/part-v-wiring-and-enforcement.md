# Part V — Wiring & enforcement  (SB14–SB17)

<!-- skeleton:SB14 SB15 SB16 SB17 -->

**Ceiling #2 — inert until wired.** This is the review-critical property. Every emitted boundary is an
*example/launcher the operator must activate*: merge the settings block, set `GOOSE_SANDBOX`, or **wrap**
the process. agentteams deliberately never writes the operator's live config. Read-only verifiers
(`verify_sandbox_wiring`, `verify_goose_sandbox_wiring`) exist precisely to detect the "looks confined,
enforces nothing" state — and they are output-only (never echo live-config secrets).

The **PreToolUse hook** is the second surface and is honestly framed as a **best-effort speed-bump, not
a boundary**: it gates a named subset of destructive `Bash` spellings and explicitly does *not* cover
`Write`/`Edit` deletes, non-Bash tools, obfuscation, or non-honoring harnesses. Its default is
**fail-open** (a crash allows), flipping **fail-closed** only under `confined`/`exclusive`. A reviewer
should read a green delete-gate suite as "these spellings are gated," never "deletion is prevented."

*Detail:* [Reference Part V](../reference/part-v-wiring-and-enforcement.md).
