# Part IV — The mechanisms  (SB10–SB13)

<!-- skeleton:SB10 SB11 SB12 SB13 -->

Three emitters, three trust models — a reviewer should not treat them as equivalent:

- **A — claude settings block** (any OS). Emits `allowWrite`/`denyWrite`/`denyRead`/
  `allowUnsandboxedCommands:false`. **It emits no egress directive** — claude network confinement is
  Claude Code's *product default*, which agentteams neither emits nor verifies. Its Linux product arm is
  unverified.
- **B — goose Seatbelt** (macOS). `deny file-write*` + `deny network*` by default; read-exclusion under
  exclusive. **Enforcement- and profile-syntax-unverified off a mac.**
- **C — the dual-OS launcher** (any framework). **Linux `bwrap`** branch: `--ro-bind / /`, scratch-only writes, `--unshare-net`,
  and **credential tmpfs masks on every wrap** (not just exclusive) — the **one enforcement-verified**
  path. **macOS `build_macos`** branch (2026-W36, any non-claude/non-goose framework): `sandbox-exec` +
  a Seatbelt profile, RLIMIT_CPU/NPROC caps, loopback-only proxy, non-exhaustive setuid denylist —
  **enforcement-UNVERIFIED** until `mac-escape-tests.sh` passes (memory UNCAPPED, no syscall filtering).

On Linux, C **stacks** with **A only** — a claude Linux team emits both the settings block and the
launcher. Goose's Seatbelt (B) is **macOS-only** (an explicit `darwin` guard) and never coexists with the
Linux launcher, so goose-on-Linux has the launcher alone, not a doubled boundary. The launcher is
framework-neutral — emitted for *every* framework, no harness preference.

*Detail:* [Reference Part IV](../reference/part-iv-the-mechanisms.md).
