**Layer → what it buys → its ceiling.**

| Layer (Part) | What it buys | Its honest ceiling |
|---|---|---|
| Constitution (II) | non-overridable principles that outrank every other instruction | states it is **not self-enforcing**; presence/reachability audited, decision rule is judgment (S4) |
| Sentinel + triad (II–III) | gated *decisions*: PASS/CONDITIONAL PASS/HALT + signed clearance/waiver/grant | sentinel is a **fallible LLM** except where S-1/S-8 scanner backs it; symmetric HMAC stops a **keyless** forger, not a key-holder (S5–S10) |
| Gates (IV) | block *destructive execution*, fail-closed, at CLI entry points | guard **CLI entry points only**; `--migrate` exempt via one explicit in-process parameter (S11–S14) |
| Scanner (V) | block *bad content* — credentials/PII/paths/injection, deterministic | **shape-blind**; only **high** blocks; **no formula/CSV-injection detector** exists (S15–S16) |
| OS confinement (VI) | bound *runtime reach* via sandbox + `denyWrite`/`denyRead` | **opt-in**, **inert until wired**, **Linux-verified only; macOS Seatbelt unverified** (S17–S19) |
| Intel + red team (VII) | keep controls *current* (24h TTL feeds) and *tested* (self-auditing cycle) | snapshot is "valid as of," not a baseline; a control is meaningful only against its **stated tier** (S20–S21) |
| Integrity + backups (VIII) | make tampering *evident* and damage *recoverable* | manifest is a **speed bump, not a boundary**; a missing manifest is "never set up," not "compromised" (S22–S24) |
