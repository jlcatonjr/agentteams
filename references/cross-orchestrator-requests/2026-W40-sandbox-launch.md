# Coordination Request — 2026-W40 Sandbox Launch

**From:** agentteams orchestrator · **Date:** 2026-09-28 · **Protocol:** 3 (orchestrator-to-orchestrator)
**Clearance:** `@security` CONDITIONAL_PASS (security-decisions.log.csv, 2026-09-28)
**Channel:** mediated — this artifact is the request; no direct cross-repo write occurs until the
target orchestrator (operator) approves.

Both target repos carry their own orchestrator and are registered `manual_review` / Protocol 3, so
their changes are proposed here, not written directly.

---

## Request A — researchteam (satellite)

**Goal:** enable OS write-confinement natively, without opening a control-plane self-tampering hole
and without shipping confinement that bypasses the constitutional gate.

Proposed changes (one atomic unit — apply together or not at all):

1. **`.gitignore`** — add `.claude/settings.json` **below** the managed fence (closing marker
   `# <<< researchteam:managed` is at line 77). Placing it inside lines 1–77 gets it wiped on the
   next `researchteam update` (the file documents a 2026-08-07 regression of exactly this kind).
2. **`.claude/settings.json`** — author a **native** block (do not copy baseAgent's paths):
   - `sandbox.enabled: true`, `filesystem.allowWrite: ["."]`, `allowUnsandboxedCommands: false`.
   - `filesystem.denyWrite` covering researchteam's **actual** control plane (verified present on
     disk): `.claude/agents/references/security-decisions.log.csv`,
     `.github/agents/references/security-decisions.log.csv`,
     `.github/agents/references/agent-privilege.json`, `.claude/hooks/constitutional-gate.py`.
   - **Merge the shipped `PreToolUse` gate block** from `.claude/settings.hooks.example.json` into the
     same file, so confinement ships *with* the constitutional gate (the script already exists at
     `.claude/hooks/constitutional-gate.py`; only the wiring is missing). Confinement without this
     leaves the E3 content-scan gap open.

**Why (adversarial R1/R2/R3):** `allowWrite:["."]` with no `denyWrite` would let a confined agent
rewrite its own security log / privilege roster / gate script; and confinement without the gate is a
false sense of protection.

## Request B — baseAgent (bridge satellite, operator-owned settings.json)

**Findings to remediate (surfaced, not silently edited):**

- **SB-F1** `.claude/settings.json` is **tracked in git** although its own comment says
  "operator-owned and gitignored." Proposed: `git rm --cached .claude/settings.json` + add it to
  `.gitignore` (agentteams convention gitignores all of `.claude/`). Contents are currently clean
  (no absolute paths), so this is hygiene, not an active leak.
- **SB-F2** the settings.json comment cites "macOS Seatbelt" — wrong for this Linux host (Claude
  Code confines via bubblewrap here). Proposed: correct the comment.

baseAgent's native `sandbox` block is already `enabled:true`; no functional change is requested,
only the two hygiene fixes above.

---

## Honesty ceilings on this launch

- **Inert until restart** — native `sandbox` blocks engage only on the operator's next Claude Code
  restart; they cannot be made live in-session.
- **Tested target** — the in-session enforcement + mediated-comms tests exercise the `confine-run.sh`
  **launcher** (bwrap, enforcement-verified), not the settings.json block, which stays untested until
  restart. The test must state this and include a **negative control** (a sibling-repo write must be
  DENIED while the in-repo coordination-artifact write succeeds and git egress works).

## Approval

Operator (as target orchestrator) selects per repo: **apply now (agentteams orchestrator as
delegate)** / **operator applies** / **record finding only, defer**. On approval, execute steps 10–14
of `tmp/by-week/2026-W40/sandbox-launch.steps.csv`; `@security` then flips `conditions_verified`.
