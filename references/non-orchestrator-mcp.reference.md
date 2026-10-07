# MCP Servers for Non-Orchestrator Agents under `write_policy: "orchestrator-only"`

**Status:** current as of agentteams `main` after PRs #123, #124, #127 and the MCP-need protocol PR (§5a),
including the @security verdict and the operator decisions of 2026-10-06 on Lean tools (§5). Updated
2026-10-07.
**Audience:** maintainers of agentteams and of teams that run the orchestrator-only-writes pilot
(mathAgents, baseAgent).
**Design of record:** `references/plans/orchestrator-only-writes-pilot.design.md` (§8–§12) and
`references/plans/orchestrator-only-writes-p4c.design.md`. Both are local plan files; this page is the
published summary.

---

## 1. The short version

Under the switch, **a non-orchestrator agent gets no MCP server, with one exception**: on Goose it gets the
first-party read-only file server, `agentteams_readfs`. Every other MCP route is either refused by the
`AR_WRITE_POLICY` audit or deliberately not built yet.

| Kind of MCP server | Example | Status for non-orchestrator agents |
|---|---|---|
| First-party read server | `agentteams_readfs` (Goose) | **Allowed.** It is the only one |
| First-party record server | `agentteams_coordination` (Goose) | **Refused.** It writes request and log files. The generator withholds it, and since PR #123 the audit refuses it too |
| First-party submit channel | `agentteams_proposals` (P4c) | **Designed, not built.** Waiting on @security and a P5 cost baseline (§4) |
| Domain read/compute server | a Lean LSP server (goal states, diagnostics) | **Held.** Refused inside the session; a runner-hosted design is cleared for exploration only (§5) |
| Third-party write-capable server | GitHub, databases, filesystem | **Refused.** It would write past every check the pilot adds (§6) |
| Operator MCP server of any kind | anything in the brief's `mcp_servers` | **Refused on non-orchestrator agents** (Goose: withheld by the generator, refused by the audit; Codex: `mcp_servers` refused) |

The orchestrator is exempt from all of this. Only the shallowest `orchestrator` file is exempt, and on
Goose the `bridge-orchestrator` recipe too.

## 2. Why the switch closes MCP by default

"Only the orchestrator writes" is enforced by narrowing every other agent to `read`, `search` and `todo`,
and checking that narrowing statically (`AR_WRITE_POLICY`). An MCP tool can do anything its server does.
The audit can't tell a read-only tool from a writing one by its name, so it treats every MCP tool on a
non-orchestrator agent as an error.

An MCP server also runs as an ordinary process launched by the host session, so it sits **outside** the OS
sandbox the runner uses for `--run-request` commands and gates.

Writes under the switch go through one path. An agent returns a `change-proposal`, `delete-proposal` or
`command-request`. The orchestrator queues it with `agentteams --apply-proposal` / `--run-request`. The
out-of-session runner (`--serve-requests`), which alone holds the ledger key, then:
- checks per-agent scope, protected paths and the base hash;
- runs the registered gates, confined;
- writes the change and records a signed ledger row.

A write-capable MCP server on a non-orchestrator agent would skip all of that.

## 3. What PR #123 enforces (rules in force now)

For each non-orchestrator agent file under the switch, by framework:

**Claude (markdown agents):**
- **Error:** an `mcp__…` tool in `tools:`. It is not on the read-only list, and neither is any other
  unknown tool.
- **Error:** any capability front-matter key other than `tools`, `model`, `disallowedTools`, `agents` and
  `permissionMode`. That means `mcpServers`, `hooks`, `skills`, `memory`, `allowed-tools`,
  `capabilities`, and any key later added to `CAPABILITY_FRONT_MATTER_KEYS`. Why each matters:
  - an inline `mcpServers` entry starts a process when the subagent starts, whatever `tools:` grants;
  - `memory` turns on Read, Write and Edit;
  - `hooks` run shell commands;
  - `allowed-tools` is a legacy grant that some hosts still read.
- **Error:** a `permissionMode` other than `default` or `plan`.
- **Error:** a front-matter key the check can't read reliably: an explicit `? key`, a merge `<<:`, an
  anchored, aliased or tagged key, or a quoted key with an escape. Any of these could hide one of the keys
  above.
- **Generation:** `narrow_tools` now removes a one-line `allowed-tools:` and refuses a multi-line one.
  Before PR #123, the Claude `team-builder.md` generated under the switch kept
  `allowed-tools: Read, Edit, Write, Grep, Glob, Bash` beside its narrowed `tools:` line.

**Goose (recipes):**
- **Allowed:**
  - `developer`, judged by the tools it grants;
  - `analyze`, only when listed with `available_tools: [__none__]`. It reads outside the workspace, and
    Goose adds it beside `developer` unless the recipe lists it, so it must be turned off explicitly;
  - `agentteams_readfs`, matched by what it launches (the shipped entry's type, `cmd` and `args`, with no
    other keys), not by its name. Under the switch the server is installed at `.agentteams/bin/goose-readfs-mcp.py`, which
    every session sandbox write-denies, and launched with `python3 -I -S`. The runner refuses to serve when
    that copy doesn't match the pinned hash. Residue: `python3` still comes from PATH; `--check-wiring`
    flags one inside the project.

  Built-in names count only with their real type: `developer` is builtin; `analyze` and `summon` are
  platform.
- **Error:** every other extension, including `agentteams_coordination`, `summon` and any operator MCP
  server.

**Codex:** the switch is refused at generation (P4a key custody: no session sandbox to protect the
ledger key). The audit still refuses `mcp_servers`, because Codex's `sandbox_mode` doesn't confine them.

**Copilot and agents-md:** the switch is refused at generation, for the same reason (decision E). So on a
team whose main surface is Copilot (or Codex), "only the orchestrator writes" isn't mechanically enforced on
that surface. It rests on the agents' declared tools and instructions alone, and a multi-surface brief
renders those surfaces outside the pilot's guarantee (`write_policy_frameworks`).

**Shells:** generated non-orchestrator agents are narrowed to `read`/`search`/`todo`, so they hold no
shell. An adopted agent that still declares one (Claude `Bash`, Goose `shell`) gets a **warning**, not an
error. To make "no agent-held shell" hold for adopted agents, drop `execute` from them; their commands go
through the runner anyway.

**Briefs that render several frameworks** (since P5a, PR #124): `write_policy_frameworks` scopes the switch
to `claude` and/or `goose`. Other frameworks render without it, outside the pilot's guarantee. The switch
also needs an explicit `privilege_profile` of `confined` or `exclusive`.

To check an existing team, run the post-generation audit on it (`--post-audit`) with the brief's
`write_policy` set. Agents adopted from a package, such as the mathagents pipeline agents, are checked too.

## 4. The planned channel: `agentteams_proposals` (P4c, not built)

**The problem it solves:** the proposal's full content currently travels inside the subagent's reply. So it
always enters the orchestrator's context, and the orchestrator then writes it out again to a file
for `--apply-proposal`. Per proposal of N tokens, that is about:
- N input tokens, carried for the rest of the session;
- N re-emitted output tokens;
- one copy that nothing checks against the agent's original.

**The design:**
1. A first-party stdio MCP server lets the agent submit the artifact directly. It holds no key and only
   writes `stage-proposal` requests to the queue.
2. The runner verifies the dispatch nonce, strips it, and stages the artifact under the write-denied
   control plane with its content hash and a dry-run preview.
3. The orchestrator gets a short receipt and approves by id (`--apply-staged <id>`). That re-runs the full
   check path.

On Claude the server would be declared **inline per subagent**, in agent front matter (not `.mcp.json`),
with exactly four tools listed by name. On Goose it would get its own extension set.

**What it changes:** the invariant becomes "only the runner writes, and only what the orchestrator
approves". The orchestrator approves from a receipt, not from the content.

**Gate to build it:**
- the P5 measurement (pilot design §12; `apply-proposal` ledger rows now record `bytes`) shows the
  carrying cost is material;
- @security answers the six questions in the P4c design, among them server integrity at launch and
  whether blind approval is acceptable.

## 5. Domain servers (held)

A Lean LSP server such as lean-lsp-mcp would help a prover agent a great deal: goal states and diagnostics
without a `lake build` round trip through the runner. But every LSP tool, `lean_goal` and
`lean_diagnostic_messages` included, compiles the file, and compiling Lean can run arbitrary code. Its
`lean_run_code` tool runs code outright, and its search tools reach the network.

**Decided on 2026-10-06:**
- **@security: HALT** for a server started inside the agent's session. The session can't sandbox it, and the
  runner's current sandbox profile denies writes and unlisted programs but not reading or the network.
- **@security: CONDITIONAL PASS, for exploring a runner-hosted design.** The runner would host the server
  under a stricter profile: no network, no home-directory reads beyond the toolchain, `lean/` and the pinned
  install, and no lasting writes. Tools would be granted by exact name, inputs confined to `lean/`, and
  output size-capped. The server would be installed locally and hash-pinned, never fetched at launch (no
  `uvx`).
- **The operator** accepted the remaining risk: code run while compiling can read what the sandbox still
  allows and put it into the diagnostics text. The pilot is macOS-only, and only the preparation step is
  approved. Building waits for the P5 measurement of lean-prover's round trips.

Until then, Lean checks go through `command-request`s (`lake build`, `lake env lean --stdin`) run confined by
the runner.

## 5b. The catalogue of foundational servers

Since 2026-10-07 agentteams ships a catalogue (`agentteams/templates/mcp/`, API page `mcp_catalog`):
- **On by default wherever an MCP host-feature token is set:** `agentteams-recall` (memory and code index
  queries; never refreshes) and `agentteams-gitread` (read-only git in an isolated `git`). Both are first-party
  and read-only, so Goose and Codex wire them; Claude gets them in the inert file. Codex withholds them unless
  the brief lists them in `mcp_catalog`, because Codex ignores `scope`.
- **Opt-in templates, emitted inert:** `github-read`, `github-write` (an exact tool allowlist, with no merge and
  no deletion) and `fetch`. Each is pinned to a vetted upstream release. Activating one is an operator act after
  @security vetting; making `github-write` live is Rule-15 constraint-relaxing. `fetch` must sit behind an
  egress proxy that blocks private and link-local addresses, which upstream does not do.
- **Under the switch nothing from the catalogue is emitted.** The GitHub servers wait for P5's signed grants.
- **Before activating a catalogue server** (an operator act; @security, 2026-10-07):
  - compare the pinned binary's `tools/list` with the entry's tool list; for `github-write`, with its exact
    `--tools` allowlist (v2.0.1 consolidates issue writes into `issue_write`);
  - fill `pin.digest` with the release's sha256 or the image digest; never activate a GitHub server without it;
  - activate `fetch` only with `--proxy-url` pointing at a proxy that refuses private, loopback and link-local
    addresses;
  - `agentteams-recall` and `agentteams-gitread` run `agentteams` from PATH in the directory the client launches
    them in; gitread refuses unless that is the work-tree root.
- **`gh pr merge` is routed to the operator** by the constitutional gate where hooks run. `pr-notifier` has no
  shell, and neither PR agent is in `github-write`'s scope.
- **A GitHub opt-in (or `pr_management: true`) adds the `pr-manager` and `pr-notifier` agents.**

## 5a. Deciding which agent needs a server

Teams under the switch ship `references/mcp-need.reference.md`, the agent MCP-need protocol, and a
`references/mcp-needs.csv` register.
- An agent that hits a capability gap attaches a gap note to its handoff.
- The orchestrator records it as unverified.
- Need is decided per agent from the runner's ledger: repeated, costly commands and proposal sizes, never
  the agent's own account.
- The rule applies the security hard gate first, then a measured threshold, then a runner-path fix (a
  broader command entry or a gate), and only then a server, with exact tool names.

## 6. Giving an agent application-specific capability today

Use the proposal pipeline, not an MCP grant. All of the following is set in the project brief:

| Need | Brief field | Example |
|---|---|---|
| Check content before it is written | `proposal_gates` | `{"lean-draft-scan": {"glob": "lean/**/*.lean", "argv": [..., "{file}"]}}` |
| Let an agent run a specific command | `agent_policies.<agent>.commands` (a list) | `[{"prefix": ["lake", "build"], "args": ["^MathAgents(WIP)?$"]}]`; add `"stdin_gates": [...]` to gate piped content |
| Confine what that command may run and write | `confined_programs.<agent>` | `{"exec": ["<toolchain bin>/lean", "<toolchain bin>/lake"], "write": ["lean/.lake", "tmp"]}` |
| Limit where an agent's proposals may land | `agent_policies.<agent>.write_scopes` | `["lean/MathAgentsWIP/"]` |
| Make a file changeable only by its script | `protected_paths` | `["reports/dossiers/*/counterexamples.json"]` |

An operator MCP server stays available **to the orchestrator**. `AR_WRITE_POLICY` doesn't check the
orchestrator's tools, so a write-capable server there writes past the ledger. That is the operator's
responsibility. Prefer a command entry the runner can confine.

## 7. References

- PR #123 (merge `2f73596`): the capability-key check, `memory`, unreadable YAML keys failing closed,
  legacy `allowed-tools` stripping, the Goose coordination allowlist, the orchestrator wording, ledger
  `bytes`.
- PR #124 (merge `f113f5d`, P5a): `write_policy_frameworks`, the explicit `privilege_profile`, the
  orchestrator-duties check, and user regions kept on `--overwrite`.
- `docs_src/api-reference/proposals.md`: the `AR_WRITE_POLICY` tables.
- `docs_src/api-reference/write-policy.md`: `narrow_tools` and `apply`.
- `references/plans/agent-scoped-mcp-write-access.report.md`: the investigation behind this page. It and the
  two design files above are local plan files (not published), so §4 and §5 summarize them here.
