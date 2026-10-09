# Part V — Wiring & enforcement  (SB14–SB17, SB24)

<!-- skeleton:SB14 SB15 SB16 SB17 SB24 -->

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
Since PR #173 the hook also asks before a **PR merge** — `gh … pr … merge`, and the REST `pulls/<n>/merge`
endpoint or the GraphQL `mergePullRequest`/`enablePullRequestAutoMerge` mutations sent by *any* client
(`curl`, `wget`, httpie, an interpreter). Same ceiling: a text match over one `Bash` command, so an
obfuscated or split call evades it.

## SB24 — The orchestrator-only write policy and the `agentteams_runner` server  ✅/⚙

**The trust model.** Under the opt-in `write_policy: "orchestrator-only"`, a non-orchestrator agent loses
every write, shell and dispatch tool (narrowed to `read`/`search`/`todo`) and either returns typed
proposals to the orchestrator or — if the brief grants it — writes through the `agentteams_runner` MCP
server. Neither the orchestrator nor the server holds authority: both only **queue** requests. The
authority is an **out-of-session runner** the operator starts outside every agent session; it alone
holds the ledger key (a 0600 file under `~/.config/agentteams/keys/`, read-denied by the session
sandboxes; it refuses a key in its environment), re-validates every request (identity from the dispatch
nonce, scopes, allowlist, gates, base hashes), and runs commands and gates in an OS sandbox. A request's
channel is derived from the directory it arrived in, not from anything the sender writes.

**Key properties a reviewer should check.**
- The switch is refused unless the session sandbox is in play: claude or goose with an *explicit*
  `confined`/`exclusive`; codex only when launched through its own launcher + role gate (unverifiable
  at generation); never Copilot or agents-md.
- The MCP server is a **keyless queue client**, explicitly *not* a trust boundary. Its installed copy
  sits in the write-denied control plane, is launched by an absolute system `python3 -I -S`, and is
  checked by the runner against a pinned SHA256 on every poll (detection — prevention is the sandbox's
  write-deny). Its read tool denies VCS, control-plane, queue and credential paths on both the requested
  and the resolved path.
- Writes are **staged** for the orchestrator by default. Only the runner can upgrade one to a direct
  write, and only under an **operator Ed25519-signed grant** that verified at start (bound to agent,
  tools, scopes, gates and the server hash; ≤30 days, ≤500 writes, ≤3 active or none used). **Deletes
  are always staged.** This is the repository's Rule 15 pattern: a constraint-relaxing grant must be
  asymmetric-signed, bounded and capped.
- `AR_WRITE_POLICY` audits that no non-orchestrator agent can still write; `--check-wiring` checks the
  live configuration the layer depends on (no shadowing server, Claude denies/`denyWrite`, Goose
  profile).

**Honest status.** ✅ in code and tests (runner, channels, staging, grants, server pin and deny list,
audit, wiring checks, interop via `--interop-from --description`, PR #174). ⚙ **Not yet verified:** that
Claude Code and Goose actually launch the inline server on a live host (tests drive it as a subprocess;
the mathAgents M6 pilot is that test). `--check-wiring` checks configuration, not behaviour. Staged mode
cannot support the agent's own edit-test loop.

**Ceiling.** The runner and the operator's host are the TCB. The layer narrows what a mis-steered agent
can do; a same-host principal who can read the ledger key or holds the operator's signing key is out of
scope — ceiling #4, not a new one. It holds only where the session sandbox is wired (ceiling #2).

*Detail:* [Reference Part V](../reference/part-v-wiring-and-enforcement.md).
