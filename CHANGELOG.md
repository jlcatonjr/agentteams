# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### feat (sandbox operator handoff: a sandboxed agent hands refused work to the operator as a script and a complete command)

- An agent inside an OS sandbox (Claude Code, Codex `workspace-write`, `confine-run.sh`) that hits a refused
  operation (a denied network host, `Operation not permitted` on a `.git` gitfile or outside its roots, an
  unreachable credential socket) now writes a reviewable `<slug>.handoff.sh` in the plan's week folder and gives the
  operator the **complete command** to run it in a terminal outside the sandbox, then reads the result back
  from `<slug>.handoff.log` and continues.
- New `agentteams/sandbox_handoff.py` (stdlib), `python -m agentteams.sandbox_handoff detect|write|status`:
  - The script carries a header (purpose, `blocked-by`, `touches`, destructive + clearance, plan), uses
    `set -euo pipefail` and an absolute `cd`, checks for required tools, echoes every command, prompts `y/N` by
    default (always when destructive), runs `verify` commands, and refuses to run while a sandbox marker is set.
  - The complete command re-checks the script's SHA-256 before running it, so an edit made after the operator
    reviewed it refuses to start.
  - `write` refuses a destructive spec without a recorded `@security` clearance (C-5), any trust root or
    capability grant (settings/hooks, rosters, `management-authority.json`, the integrity manifest, signing
    keys), credential-shaped text, and a script that fails `bash -n`.
  - `status` reads `ok` / `failed rc= step=` / `incomplete` / `not-run`, and `stale` when the log came from a
    different version of the script.
- New always-emitted `references/sandbox-handoff.reference.md`. It covers channel order (the intended in-grant
  path, then the proposal runner, whose refusal is a stop, then a handoff), the script and message contracts,
  governance, plan integration, resuming, and common handoffs.
- Orchestrator `available_workflows` v=5 → v=6:
  - A new **Sandbox Operator Handoff** sub-protocol that any workflow step can append. It is named rather than
    numbered, so it cannot collide with a project's own workflow numbers.
  - A sandbox augmentation table across Workflows 0/4/7/8/9/11 and git operations.
  - Workflow 0's capability-gap check gains a sandbox branch.
  - Plan documentation gains `operator` steps.
  - In Workflow 11, gates 7/7b run through a handoff when GitHub is unreachable; "unreachable from the sandbox" is
    no longer an "otherwise skip".
  - A new gate 7c reports pending handoffs. While work depends on a handoff, the session closes as *awaiting
    operator handoff*.
- `@git-operations` gains Invariant rule 10, a Sandbox Augmentation table and a `Sandbox handoff` output-contract
  line.
- `parallel_plan`: a step with `agent` = `operator` is always a singleton wave and is never a coordination
  candidate. The parallelization reference lists it among the carve-outs.
- The CI/CD gate's "run status is reachable" condition now counts reachability through a handoff:
  - The orchestrator's Workflow 11 step 7, `@git-operations` rule 7 and `github-workflows-merge.reference.md` are
    aligned on this.
  - The handoffs use the REST `actions/runs` query first and fall back to `gh run watch`.
  - Step 8's terminal-act ordering also admits a session that closes as awaiting operator handoff.
- Under `write_policy: "orchestrator-only"`:
  - Only `@orchestrator` writes handoff scripts.
  - "Not allowlisted" counts as a runner refusal, which is a stop and is never re-routed through a handoff.
- New API page `docs_src/api-reference/sandbox-handoff.md`.

## [1.0.1] - 2026-10-21

### release notes

agentteams 1.0.1 is a patch release on the 1.0 line: five fixes held until after v1.0.0, backported from `main`
by `cherry-pick -x`. It adds no dependencies and no new configuration. Decision logs, signed rows and generated teams
carry over unchanged.

- **Rule 15: unfreezing a frozen surface now needs the operator's Ed25519 signature.** An unfreeze is derived from
  any of the row's text columns, or from a write target inside a frozen surface. An author can no longer
  self-declare it away. researchteam 1.0.1 refuses every unfreeze until agentteams has this.
- **A long `run_command` returns its result instead of "queued"** (mathAgents M6-F5). The call waits up to 180 s
  for its own result.
- **The Claude hook's notice log is no longer an undeclared write** during a long confined command (M6-F7).
- **`--update --merge` draws the pipeline graph from the files it wrote**, so adopted agents appear in it.
- **Merge-adopted agents are listed as rostered, not as orphans to delete.**
- **Re-pins:** `references/enforcement-integrity.json` re-pins the changed enforcement modules. Re-render, then run
  `--verify-integrity`.
- **Install:** run from a verified, detached checkout of the signed `v1.0.1` tag, and pin its peeled commit (release
  key `SHA256:IPdnLwlF…`, as in `SECURITY.md`).

### fix (Rule 15: an unfreeze of a frozen surface derives constraint-relaxing; it is never self-declared)

- Before, a decision row that unfroze a surface was relaxing only when its author set `effect_relaxes`, which is
  the self-declaration Rule 15 forbids. researchteam 1.0.1 refuses every unfreeze until agentteams derives it.
- `cli/effect_classifier.py` now derives it on two axes:
  - **Text.** An unfreeze named in any of a row's text columns (`scope`, `action_reviewed` and `decision`, not
    only the first non-empty one) derives relaxing: `unfreeze`, `--unfreeze`, `unfrozen`, `un-freeze`,
    `un_freeze`, `thaw`. `management_directives.scope_is_allowed` refuses the same words, so no directive can
    authorise one.
  - **Target.** `derive_effect_class`, `classify_row`, `is_relaxing` and `requires_operator_signature` take an
    optional `frozen_paths`, the paths owned by a `frozen_surfaces` list (shared contract with researchteam,
    point 4). An `effect_targets` entry that is, is under, or contains one of them derives relaxing, whatever the
    action is called; `..` segments are resolved first.
- An unfreeze is in the operator-Ed25519 subset (`requires_operator_signature`), so an HMAC row can't authorise it.
- The frozen set a caller passes is the recorded freeze state before the row's change, never the working brief,
  which an agent can edit. A surface id dropped from that state is an unfreeze even when no row names it
  (`frozen_surfaces.unfrozen_ids`).
- The owned paths and the unfreeze vocabulary live in the new `cli/frozen_surfaces.py` (stdlib only; its ids are
  pinned equal to the framework registry by a test). No caller passes `frozen_paths` yet, because agentteams has
  no `frozen_surfaces` brief key; the text axis holds without it.
- `cli/effect_classifier.py`, `cli/management_directives.py`, `integrity.py` (which now lists
  `cli/frozen_surfaces.py`) and the new module change, and their pins in `references/enforcement-integrity.json`
  move; the operator's merge approval covers that.

### fix (runner MCP: a long `run_command` returns its result, not "queued")

- A granted agent's `run_command` waited only the server's call wait (20 s by default) and then returned
  `{request_id, status: queued}`. Grants are exact, so an agent without `request_status` could never read the result
  (mathAgents M6-F5: a 31 s `lake` build).
- `run_command` now waits for its own result for up to 180 s, which is the runner's 120 s MCP command cap plus time
  queued. It still ends early when the runner stops beating. Writes and deletes keep the short call wait.
- A `queued` reply now tells an agent without `request_status` to report the request id in its handoff. No grant
  changes: `request_status` is still granted only when the brief lists it.
- **Limits:**
  - The wait is not a guarantee. The runner serves one request at a time, so with several agents queued, or an
    uncapped orchestrator-channel command ahead, a result can still take longer than 180 s and come back `queued`.
  - The MCP client's own tool-call timeout must be longer than 180 s. Goose's runner extension sets 300 s; for
    Claude Code, check `MCP_TOOL_TIMEOUT`.
  - The server handles one call at a time, so while a `run_command` waits, that agent's other runner calls
    (`write_file`, `request_status`) wait behind it.
  - The server ignores cancellation, so a call the client abandons loses its output. The runner's ledger keeps the
    command's exit status and undeclared writes, not its output.
- The agent guidance (`write_policy.py`) now says this. `agentteams-runner-mcp.py`, its pin in `runner_mcp.py` and
  `write_policy.py` change, and the operator re-pins `references/enforcement-integrity.json`.

### fix (runner: the Claude hook's notice log is not a confined command's undeclared write, M6-F7)

- The generated Claude hook appends `.claude/hook-notices/<UTC date>.log` while a long confined command runs.
  `run_request` watches `.claude`, so such runs were reported ok:false with that file as an undeclared write.
- Now exempt, under the same rule as the runner's heartbeat (M6-F6), only when a sandbox ran. The exemption covers only
  `.claude/hook-notices/<date>.log` for the UTC day or days the command spanned, and only when the snapshots show it
  was appended to or created. That means the same inode and a size no smaller, still a regular file: never a
  truncation, an inode replacement, a deletion or a symlink. Every other `.claude` path stays watched,
  `.claude/settings.json` included, and nothing is exempt in an unconfined run. Rule 12: this exempts one log file
  from the escape backstop.
- `agentteams/proposal_run.py` changes. Its pin in `references/enforcement-integrity.json` moves too, and the
  operator's merge approval covers that.

### fix (`--update --merge` draws the pipeline graph from the files it wrote)

- The graph was built before the write, from freshly rendered agents laid over the disk copies. A merge keeps the
  on-disk front matter, such as an adopted agent's place in the orchestrator's `agents:` list, so the graph described
  files that were never written. baseAgent lost 13 adopted agents' edges and gained 6 that weren't in `agents:`, and
  a standalone `--refresh-graph` then flipped the graph back.
- After a successful merge-mode write, the graph is now redrawn from disk (`git_hooks.redraw_graph_after_merge`), the
  same way `--refresh-graph` and the commit hook build it. This happens before the build log is written.

### fix (orphan advisory: merge-adopted agents are not "orphans to delete")

- An agent adopted with `--update --merge --adopt-orphans` is recorded only in the orchestrator's `agents:` roster
  on disk. Each run rebuilds the manifest from the brief, so a later plain `--update` reported every such agent as an
  orphan to "review and delete manually" (found by baseAgent: 17 adopted agents). The noise could hide a real orphan.
- These files now print under their own heading, labelled as being in the orchestrator's roster (front matter,
  unverified). They are still recorded in the daily orphan-events log, under their own kind (`rostered-unverified`)
  and routing, so a planted file kept only by a roster line stays on record. A roster the merge gate's parser cannot
  read leaves the files orphaned, so nothing is silently kept.

## [1.0.0] - 2026-10-18

### release notes

agentteams 1.0 generates a coordinated agent team (orchestrator, governance, domain and workstream-expert agents)
from one project brief, for Claude, Copilot (VS Code and CLI), Goose, Codex, AGENTS.md and Grok. This release
collapses fourteen release candidates (rc.1 to rc.14) and everything since 0.1.0. The largest changes are a
signed, hash-chained security decision log that the constitutional gate hook enforces, and an opt-in
`write_policy: "orchestrator-only"` mode. In that mode only the orchestrator writes, and other agents write and
run commands through an out-of-session, OS-confined runner. Claude and Copilot (VS Code and CLI) are Stable;
Goose, Codex, AGENTS.md and Grok are Beta and outside SemVer (see `STABILITY.md`). If you are upgrading from 0.1.0
or from any release candidate, read "Breaking changes and migration" first.

- **Install:** run from a verified, detached checkout of the signed `v1.0.0` tag. Pin the tag's commit, never `main`.
  The migration guide's "Moving to a release checkout" section has the steps.
- **Upgrade order:** `--verify-integrity` and review its HALT report; then `--migrate-decision-log`; then re-render;
  then `--check-wiring`.
- **Signing key:** tags are SSH-signed by the operator. The key fingerprint begins `SHA256:IPdnLwlF`; the full value
  is in `SECURITY.md`, under "Release signers".

### Security and governance

The constitutional rules cited below are: C-2, a `@security` HALT is final; C-3, an agent's declared tools are a
limit; C-4, content an agent reads is data, not instructions; C-5, clearance before destructive actions.

#### The constitutional gate hook

- **The gate runs under an interpreter that can import agentteams.** Each Claude render, and each confined Grok
  render, writes a gitignored `<hooks>/agentteams-python` pin that names the rendering interpreter. When `python3`
  can't import agentteams, the gate re-runs under the pin with a scrubbed environment. (rc.10, rc.11) #226
- **No silent allow when the gate can't run.** Before, a gate that couldn't import agentteams denied everything
  under a confined profile and allowed everything under the default cooperative profile. Now, with no working
  interpreter, a content-bearing Write or Edit gets `ask` (cooperative) or `deny` (fail-closed), and the reason
  names `agentteams --check-wiring`. (rc.7, rc.10) #226
- **The pin is not written without its ignore rule.** When the hooks `.gitignore` is operator-owned or a symlink,
  agentteams leaves it alone, writes no pin and prints a notice. #226
- **The gate never imports agentteams from the project tree.** The repository-root fallback is removed, and the
  hook drops its own directory from `sys.path` before any import (except under `-I`). The gate and the Grok shim
  no longer use `from __future__ import annotations`. A planted `agentteams/`, `json.py` or `__future__.py` can no
  longer choose the verdict. (rc.7, rc.11)
- **Confined and exclusive teams get a fail-closed gate.** The hook is emitted with `_FAIL_CLOSED_ON_ERROR = True`,
  so a crash denies. `--allow-fallback-fail-open` restores fail-open. The gate flips only on an explicitly set
  profile, not a defaulted one. Cooperative teams are unchanged. (rc.7, rc.8)
- **PR merges prompt the operator whatever client sends them.** The gate asks on `gh [flags] pr [flags] merge`,
  the REST `pulls/<n>/merge` endpoint and the `mergePullRequest` / `enablePullRequestAutoMerge` mutations, from
  `gh`, `curl`, `wget`, httpie or an interpreter, including backslash-newline splits. "merge" in a PR body or a
  later `git merge` no longer prompts. (rc.9) #150, #173
- **Branch deletions ask.** The gate asks before `agentteams --branch-cleanup` or `--branch-post-merge … --apply`
  (see "Branch lifecycle").
- **Outbound sends ask.** Uploads, requests built from command output or naming credentials, raw sockets, remote
  copies, gists, new or repointed git remotes, pushes to a URL, DNS lookups built from output and interpreter POSTs
  get `ask`. Decoding is not gated. #211
- **Git config writes ask.** A write to any git config file is `ask`, after the content scan, so a credential in
  it is still denied. #211
- **A timed-out scan no longer allows the command.** Superlinear patterns are rewritten. Commands over 32,768
  characters, or with lines over 8,192, go to the operator unscanned. #211
- **Opt-in per-case gate decision log.** With `AGENTTEAMS_GATE_LOG` set to an absolute path outside the project
  and any work tree, the hook appends one hash-chained JSON line per call. `AGENTTEAMS_GATE_LOG_KEY` adds an input
  HMAC. Unset, the hook behaves as before. #217
- **The gate decision log holds no content.** It records the decision, reason id, scanner categories, what was
  checked and the hook's sha256, never written content, reason text or exception text. Relative, in-project,
  symlinked or non-regular log paths are refused, and a failed log write never changes the decision. #217

#### The decision log, clearances and HALTs

- **Decision signing is on by default.** The `enforce_decision_signing` brief field (default `true`), emitted in
  `references/agent-privilege.json`, makes the gate refuse unsigned authorizing rows (PASS, CONDITIONAL,
  HALT-RETRACTED). It turns on at `--update` with a notice naming the opt-out. An absent file counts as off; an
  unreadable one fails closed. (rc.7)
- **The signing switch follows the brief.** Every render and projection rewrites an owned switch file to match
  the brief. It never relaxes a signing-governed team, and prints `SIGNING SWITCH RELAXED` or `KEPT`. (rc.10) #208
- **A signature covers the whole decision.** Signed payloads cover `scope` and the `effect_*` columns, and a new
  optional `text_digest` column binds the `conditions` and `evidence` text. A row whose text was edited after
  signing stops clearing. `--sign-decision` binds the digest before signing, and refuses a constraint-relaxing
  row in a log with no `text_digest` column. A `|` in a signed axis is refused. (rc.9, rc.11)
- **An Ed25519-signed row is verified as Ed25519 for every authorizing verdict.** The gate dispatches on the
  row's signed `sig_scheme` and verifies against `references/authorized-verify-keys/<key_id>.pub.pem`, whatever
  `enforce_decision_signing` is. A failed check never falls back to HMAC. Operator Ed25519 retractions clear, and
  no HMAC secret is needed where an agent runs a render. (rc.11)
- **Signed logs chain.** `--sign-decision` fills `prev_digest`. Logs left with empty links by the old signer
  verify through a disclosed legacy prefix. `--verify-integrity` reports the prefix length and any HALT inside
  it, and exits 1 on a broken chain. #224
- **New signed logs keep the legacy columns first,** and the Goose coordination MCP server no longer rewrites the
  header and refuses a hash-chained log. #224
- **`--migrate-decision-log` upgrades a legacy log.** It appends the missing signing columns (never
  `prev_digest`) and an empty `text_digest`, pads short legacy rows, refuses torn rows, and checks that each signed
  payload is unchanged. It backs up first, writes atomically, is idempotent and needs `--yes`. (rc.10, rc.11)
  #212, #224
- **`--migrate-decision-log` reports what stops clearing.** `--dry-run` lists the unsigned PASS and retraction
  rows that will stop clearing. The report names each revoked retraction whose HALT blocks again
  (`"reactivates_halt": true` in `--json`). (rc.10, rc.11)
- **A HALT blocks every action named in its id.** A HALT matches when an action id appears as a whole token in
  its id, so `m6-overwrite-render-1a2fad6` blocks `overwrite`. Only an exact-id `HALT-RETRACTED` row clears a
  token-matched HALT. Clearing-side matching stays strict. #225
- **An unauthenticated retraction no longer wedges the HALT scan.** A `HALT-RETRACTED` row that fails
  authentication or has no evidence retracts nothing and is skipped. The HALT stays open until a valid
  retraction, and the error names each skipped row and why. (rc.12)
- **Constraint-relaxing exceptions are derived, elevated and bounded.** Whether a decision relaxes a constraint
  is derived from its effect, never self-declared. The HALT scan follows lineage. Relaxing exceptions need the
  operator's Ed25519 signature, trust-root targets are refused even when signed, and an exception registry
  enforces an aggregate cap and sunset. New: the `signing` extra, `--sign-decision`, `--audit-exceptions` and
  `--list-exceptions`. (rc.8) #46
- **A superseded clearance can't be replayed.** The newest matching decision row decides; if it is consumed, the
  gate refuses. A pre-recorded clearance yields one use. (rc.9)
- **Signing needs explicit confirmation.** `--sign-decision` and `--sign-grant` show the payload and a review
  sha256, then need `[y/N]` on a TTY or `--confirm-review-sha256 <digest>` without one, before the key is read.
  `--yes` never applies. Control, bidi and zero-width characters are refused. (rc.8) #9
- **Signing is refused from an untrusted install.** It refuses when the package, `sys.prefix` or `sys.executable`
  is inside the cwd, a git work tree or an allowWrite root. `--allow-checkout-signing` overrides, warns every time
  and is recorded in the signed row. The signing code lives in the pinned `cli/operator_signing.py`, and its
  closure is checked against the integrity manifest before the key is read. (rc.8) #10
- **`--sign-decision` checks its target.** It refuses an `--output` that is not a team directory and verifies each
  signature against the team's public key before appending. The key-provisioning helper needs `--team-dir`.
  (rc.8)
- **Governance logs are validated.** `--verify-integrity` detects malformed append-only CSV logs (field count,
  unclosed quote, swallowed rows) and exits non-zero. Appends are one locked, validated write. Security-gate and
  red-team ledger rewrites are atomic. (rc.7, rc.9) #159
- **Plain `--update` doesn't need a clearance; `--overwrite` does.** The destructive-overwrite gate fires only on
  `--overwrite`, which needs an `overwrite` clearance. `--revert-migration` needs none, so a failed `--migrate` can
  always be undone. `--from-migrate`, which let the command line skip the gate, is no longer a CLI flag. (rc.1)
- **Gate refusal codes name the gate.** Failures carry `[SEC-GATE/DESTRUCTIVE:<action>]` or
  `[SEC-GATE/INTEL-FRESHNESS]`. The stale-intel refusal states how many intel-bearing placeholders it affects.
  (rc.7)
- **Threat intel is never falsely fresh.** A vulnerability cache that is unreadable, not a JSON object,
  schema-invalid or missing `generated_at` reads `stale` (`freshness.cache_corrupt`). `--security-offline` and a
  failed fetch no longer blank cached CVEs, and invalid cache reads are rejected. (rc.7, rc.10) #224

#### Keys and grants

- **HMAC signing keys must be strong.** Signing and verifying refuse a key under 32 bytes, a known placeholder
  (`...`, `changeme`, `secret`, `password`, …) or one repeated character. This covers decision rows, waivers,
  grants, management directives and the runner's proposal-ledger key (file or environment). A weak key fails
  closed, with no bypass. (rc.12)
- **The signing key is unreadable in every sandbox.** Keys live in `~/.config/agentteams/keys/` (mode 700).
  Every sandbox read-denies it, `permissions.deny` emits `Read` rules for `keys/**` and `*.pem` (Claude's built-in
  tools ignore the sandbox), and the launcher masks `--exclude` files. Write roots inside `keys/` are refused. The
  provisioning script refuses symlinked or loose key directories. Legacy `~/.config/agentteams/*.pem` is denied.
  (rc.8)
- **Only Ed25519 grants widen a sandbox.** `--sign-grant SPEC.json` issues domain-tagged Ed25519 grants. HMAC
  write grants are refused; HMAC remains for grants that don't widen. The approver roster is read only from the
  team directory. (rc.7, rc.8)
- **Cross-workspace capability grants.** `--issue-grant` and `--verify-grants` manage signed grants with an
  optional `team_id`. A grant never overrides a HALT. Grants fail closed without a non-empty
  `references/security-approvers.txt`. (rc.7)
- **Grant hardening.** The grant ledger is hash-chained, so deletion or reordering fails closed. An optional
  signed `issuer_root` rejects target paths outside it. Issue rejects empty, `/`, home-rooted or `..` targets and
  malformed `expires_at`. (rc.7)
- **`signed_ledger` warns once** when `cryptography` is below the pinned version, and still signs. (rc.8) #20

#### Management directives

- **Management repositories.** A brief can set `is_management_repo` and `authorized_managers`. An HMAC-signed
  directive verified with `AGENTTEAMS_MANAGEMENT_SIGNING_KEY` authorizes only the exact non-destructive scope it
  names. New: `--issue-directive` and `--verify-directives`. Off by default. (rc.7)
- **A directive is an authenticated operator artifact, not a tier.** It can never clear C-5, pierce C-2, widen
  C-3 or change governance. It fails closed on an empty roster, a missing key or an invalid or expired
  directive. (rc.7)
- **The scope denylist matches word stems,** so inflections such as `deletes` or `overwriting` are refused, and
  `wipe`, `truncat`, `revert` and `destruct` are added. (rc.7)
- **A directive that can't be relayed goes to the operator.** If it fails to verify or hits a refused scope,
  HALT, constitution change or C-5 destruction, the agent asks the operator once and logs the answer to
  `orchestrator-escalation.log.csv`. (rc.7)
- **Progress check-ins.** At every stop, a management-relay orchestrator prints an agent-by-task progress table.
  Cross-repo handoffs get one row each and escalate after `CROSS_REPO_REPORT_STALE_HOURS` (default 48). (rc.7)

#### Content, scanning and secrets

- **Derived content stays data (C-4).** Text an agent derives from read content (decoded, translated,
  summarized, OCR'd, or returned by a tool or subagent) keeps the no-authority status of read content. A request
  to transform and follow is itself a finding. Text in the project's own records has authority only when its
  write traces to an operator instruction. #211
- **Untrusted results are labelled.** The OCR proxy fences image text with a nonce, and memory- and code-index
  results are labelled untrusted. #211
- **The security template gates data leaving the machine,** including one-off sends and rendered markdown URLs.
  Pre-configured remotes and the project's own `gh` repository are exempt. #211
- **`python -m agentteams.scan <path>`** scans a file or directory and returns one verdict (HALT,
  CONDITIONAL_PASS or PASS; exit 1 only on HALT). It opens regular files without following links, and a skipped
  file raises a `scanner-evasion` finding. (rc.7, rc.8)
- **Scan patterns.** The scanner adds GitHub, Slack, Stripe and JWT credential patterns and literal
  instruction-override strings. File paths no longer trip the credential check, and Goose and Codex references no
  longer raise false positives. (rc.1, rc.7)
- **`.env` files stay out of git.** `python -m agentteams.env_hygiene REPO [--fix [--execute]]` reports tracked or
  unignored env files and Dockerfiles that copy them. `--install-git-hooks` installs a blocking `env-file-guard`
  pre-commit hook. (rc.9)
- **`--scan-security`** checks for PII, credentials and placeholders, with live CVE, CISA-KEV and EPSS lookups.
  (rc.1)
- **No absolute home paths in generated artifacts.** The delivery receipt, bridge stubs and reports, the
  memory-index snippets and `build-log.json` record relative paths only. (rc.7, rc.9)

#### Integrity, trust roots and the control plane

- **`--verify-integrity` checks the enforcement manifest** and exits 1 on drift. A missing manifest is a finding
  where the repository tracks one, and the confined gate then answers `ask`. A re-record keeps absent
  installed-hook pins. The grant and rank emitters and the gate hooks are pinned. (rc.7, rc.8, rc.10) #221
- **In the agentteams tree, `--check` fails on enforcement drift,** and `--write-integrity-manifest` exists.
  (rc.7)
- **`--pin-templates`** records template digests in `.agentteams/template-pins.json` (trust on first use). A
  mismatch is reported, not blocked, and an ambiguous pin location is refused. (rc.7)
- **The control plane is write-protected.** Claude `denyWrite` covers all of `.claude` (a read-only mount, so a
  rename fails) and `.codex`, `.github/agents` and `.goose` when those teams exist. (rc.7, rc.8)
- **`permissions.deny` covers trust roots:** `.claude/settings*.json`, `.claude/hooks/**`, the signing switch,
  the verify-key store, operator rosters and `management-authority.json`. Copilot and Codex teams get roster
  stubs. (rc.7, rc.8)
- **Stale protections are named.** Advisories name `denyWrite` entries that no longer exist and sibling teams the
  live settings miss. (rc.8)
- **Opt-in prompt-root protection.** `protect_prompt_roots: true` adds `Edit` deny rules for the prompt roots
  (instruction files, agent directories, `AGENTS.md`, `CLAUDE.md`, `.mcp.json` and others). `AGENTS.override.md`
  is a prompt root. (rc.8, rc.9) #8
- **Prompt-root changes are detected.** Every render records `prompt_root_hashes`. `--update` and `--check` warn
  `PROMPT-ROOT CHANGED since last build`, and `--strict-prompt-roots` makes `--check` exit 1. (rc.8) #8
- **Planted files are named.** The launcher, generation and `--check` warn about a marker-only
  `references/build-log.json` and about a `.codex/config.toml` that sets approval, sandbox, notify or MCP keys.
  Generation and `--check` warn, and `--check-wiring` fails, when `.claude/settings.json` uses
  `sandbox.excludedCommands`. (rc.8, rc.9)
- **Backups can't be loaded as agents.** Backed-up `.md`, `.yaml`, `.yml` and `.toml` files and lost-fence
  sidecars are stored as `*.agentteams-bak`; older backups are renamed in place. A restore never revives a
  sidecar. (rc.8, rc.10) #207, #224

#### Security and governance templates

- **`@security` vets dependencies before install.** It searches advisory sources and applies a 14-day release
  cooldown to every install or update in every ecosystem. Distro security channels are exempt. (rc.7)
- **More `@security` coverage:** package-install and sudo triggers, metered-endpoint disclosure, low-level
  vulnerability screening, OS hardening references, an infrastructure-layer reference, AI-code insecure defaults
  and `.env` rules. `@git-operations` never commits a `.env` file. (rc.7, rc.9)
- **Vulnerability notes in remediation plans.** `remediation.plan.md` finding rows get advisory notes from the
  cached KEV and OSV threat intel, with no new network call. (rc.7)
- **Capability keys are never applied silently.** A change to `tools`, `model`, `agents`, `hooks`, `mcpServers`,
  `permissionMode`, `skills` or `memory` is reported as a proposal with the exact line to apply. (rc.7, rc.9)
- **Tool grants are narrower.** Claude `allowed-tools` follows `tools:`, so read-only agents get no Bash, Write or
  Edit. `conflict-auditor` is read and search only. `--post-audit` warns when a non-orchestrator agent holds both
  a write tool and dispatch (`AR_WRITER_DISPATCH`). (rc.3, rc.7, rc.9)
- **Orchestrator spawn authority and query funnel.** A delegate routes user questions up to its own orchestrator,
  and an orchestrator can spawn a scoped child orchestrator. (rc.7)
- **Bridge-refresh Pre-Flight checks** are canonical in `bridge-refresh-safety.md` and bind the orchestrator,
  `@git-operations`, `@security` and `@cleanup`. (rc.7)
- **Red-team audits.** `agentteams --redteam` runs a standing red-team audit with a self-audit phase.
  `--redteam-freshness-check` (operator-only, needs `agentteams[research]`) searches for new AI-attack techniques
  and writes candidates to `references/redteam-freshness-candidates.md`. (rc.7)

### Orchestrator-only writes and the runner

#### The `write_policy` switch

- **`write_policy: "orchestrator-only"` (opt-in).** Every non-orchestrator agent is narrowed to read, search and
  todo and told "Return Proposals, Never Write". The orchestrator keeps its tools and gains an "Applying
  Proposals" workflow. The team ships `references/write-policy.reference.md`. With the field absent or
  `"default"`, output is byte-identical. (rc.9)
- **Where the switch applies.** It needs an explicit `privilege_profile` of `confined` or `exclusive`.
  `write_policy_frameworks` limits it when one brief emits several frameworks. (rc.9)
- **Frameworks under the switch.** Claude is enforced. Codex is allowed on Linux and macOS and holds only when
  launched through the launcher (see "Codex under the launcher"). Goose is refused
  (`AGENTTEAMS_ALLOW_UNENFORCED_GOOSE_SWITCH=1` is for testing only), as are Copilot and agents-md. (rc.9, rc.10)
  #204
- **The top-level session gets the dispatch contract.** `CLAUDE.md` gets "Write Policy: You Are the
  Orchestrator", covering nonces, the staged approval loop and `--verify-proposal-ledger`. Granted agents are told
  a write receipt is final. #204
- **Existing teams adopt the switch** with `--update --merge` and `--reconcile-front-matter --reconcile-apply`,
  which narrow markdown agents and add missing capability keys. Only Codex TOML needs a fresh build, or
  `--overwrite` with a recorded clearance. (rc.9, rc.10)

#### Proposals, dispatch nonces and the ledger

- **Revoking an MCP grant binds live dispatches.** Once a grant is removed and the runner restarted, the agent's
  unexpired nonces are refused on the MCP channel. Nonces issued while it held the grant carry a signed flag, and
  the orchestrator's queue refuses them too. Nonces issued before rc.14 have no flag and stay usable for up to 24 h;
  rotating the ledger key at upgrade closes that window. (rc.14) #242
- **Proposals instead of writes.** Agents return typed change and deletion proposals and command requests. The
  orchestrator applies them with `--apply-proposal` and `--run-request`. Identity comes from a signed dispatch
  nonce (`--issue-dispatch --agent SLUG`), never from the caller. Nonce uses are counted only just before an
  action, and attempts are capped. (rc.9)
- **Policy lives in the brief.** New fields: `write_policy`, `agent_policies` (write scopes, commands with exact
  argv prefixes, cwd, stdin gates), `proposal_gates`, `protected_paths` and `confined_programs`. Lint refuses
  shells, option or parent patterns and scopes that cover the brief or the control plane. Write scopes accept
  single-segment patterns. (rc.9)
- **Requests fail closed.** A proposal outside its scopes, in the control plane, stale or failing a gate is
  refused. Commands get a scrubbed environment; an undeclared write fails the run (exit 3); a timeout kills the
  process group. An empty `stdin_gates` list is refused. (rc.9)
- **A signed proposal ledger.** `.agentteams/proposal-ledger.jsonl` logs every action and refusal, HMAC-signed and
  hash-chained with a signed head anchor. `--verify-proposal-ledger` checks it. (rc.9)
- **Where the ledger key comes from.** The runner reads it only from a 0600 file in `~/.config/agentteams/keys`,
  and refuses to start with a ledger key in its environment. Other ledger operations take the key file or the
  environment (`AGENTTEAMS_PROPOSAL_LEDGER_KEY`, falling back to `AGENTTEAMS_DECISION_SIGNING_KEY`).
  (rc.9, rc.12)

#### The runner

- **The runner's own heartbeat never fails a command.** A confined command that outlasted a heartbeat tick used to
  fail with exit 3, because the runner counted its own `.agentteams/runner.heartbeat` write as the command's
  undeclared write. That file is now exempt, but only when an OS sandbox ran. A lock keeps a snapshot from ever
  seeing a half-written beat. Unconfined runs are unchanged. (rc.14) #242
- **An out-of-session runner.** `agentteams --serve-requests` runs outside every agent session, alone holds the
  ledger key, and serves a queue in `.agentteams-queue/`. Under the switch the proposal commands queue and wait;
  `--wait-result` waits again. One runner serves a project. Command output is capped and the heartbeat has its
  own thread. Restart the runner after updating. (rc.9)
- **The sandboxed orchestrator reaches the runner.** The runner publishes its heartbeat and sealed results in the
  read-only `.agentteams-status/`. Clients without a `reply_key` are refused. (rc.10) #203
- **Staged writes by default.** Agent writes are staged under `.agentteams/staged/` after every check. The
  orchestrator uses `--list-staged`, `--show-staged`, `--apply-staged SID` (re-checked against the current file)
  and `--reject-staged`. Deletions are always staged. (rc.9)
- **Direct writes only under an operator grant.** The runner applies a write directly only under a verified
  operator Ed25519 grant that binds the agent, tools, scopes, gates, server hash, expiry (30 days at most) and a
  write cap, with an aggregate cap of 3. New: `--sign-mcp-direct-grant`, `--list-mcp-direct-grants` and
  `--revoke-mcp-direct-grant`. (rc.9) #166
- **The `agentteams_runner` MCP server.** Agents named in the new `mcp_grants` brief field get exact
  `mcp__agentteams_runner__<tool>` names and the server (`write_file`, `delete_file`, `run_command`,
  `read_file_hashed`, `request_status`). It holds no key, serves one agent, launches pinned (`python3 -I -S`) from
  `.agentteams/bin/`, and records no machine path. The runner takes the channel from the queue directory and
  binds each request to its nonce's agent. (rc.9)
- **The runner names the reinstall command** when the operator's confined file is out of date. (rc.10)

#### Confinement

- **Runner commands run in an OS sandbox.** Every `--run-request` command and pre-write gate runs confined.
  Leftover processes fail the run. With no usable sandbox the runner refuses unless `allow_unconfined_runs` is
  set, which is logged. Write roots must be real, symlink-free in-project directories. (rc.9)
- **On macOS** the sandbox is Seatbelt: writes only to declared roots and a private `TMPDIR`; the control plane,
  ledger, queue and `.git` denied; the key directory unreadable; only declared programs run. **On Linux** it is
  bwrap, with the same write rules and a PID namespace, but no program allowlist. (rc.9)
- **The confined-programs file is operator-owned.** The runner reads `confined_programs` from
  `~/.config/agentteams/confined/<project>-<hash>.json`. `--install-confined FILE` installs it only on a rerun
  with a matching `--confirm-review-sha256`. Commands take a per-entry timeout. (rc.9)
- **Gate programs are bound.** `gate_exec` entries are bound to the gate argv (`gate_argv_sha256`), and a gate
  whose programs overlap a write root is refused. (rc.9)
- **macOS: xcrun launchers reach their real tool.** For a `/usr/bin` program that is an xcrun launcher, such as
  `/usr/bin/git`, the profile also allows the real tool by literal path, and for git its `--exec-path` helpers.
  Paths come from `xcrun --find` under `xcode-select -p`, and only root-owned paths are kept. `--check-wiring`
  reports each launcher's expansion and warns on unresolved ones. (rc.13)
- **Write roots over package stores warn.** A root that covers `.lake/packages`, `node_modules`, `site-packages`,
  `.venv`, `venv`, `vendor`, `.cargo/registry`, `Pods` or `.gradle` warns at `--install-confined` and each run. A
  run that also depends on an unresolved launcher is refused unless the entry sets
  `allow_unresolved_launcher_with_package_store: true`. (rc.13)
- **Claude sandbox confinement by profile.** `privilege_profile` defaults to `confined`, which emits a sandbox
  block that confines Bash and child writes to `workspace_write_roots` once merged; `"cooperative"` opts out.
  `exclusive` also denies reads of a curated set plus `protected_read_paths`. The block sets
  `"failIfUnavailable": true`. An unknown profile fails the build. (rc.7, rc.8)
- **Write roots are checked.** Generation refuses roots at `/`, `~`, `$HOME` or their ancestors, the key
  directory, credential paths, control-plane directories and paths with shell or glob metacharacters. A new root
  outside the project needs `--accept-write-root PATH`, and each added root prints `SANDBOX WIDENING`. (rc.8)
- **Confinement fails closed only where the host has no boundary.** Claude has its own sandbox, and on Linux and
  macOS the other frameworks are confined through the launcher. On native Windows and other platforms, a
  `confined` or `exclusive` build for a non-Claude framework exits non-zero unless
  `--allow-unenforced-confinement` is passed. (rc.7)
- **Fail-open settings are named.** `--update`, `--dry-run` and `--check` print a notice for a merged Claude
  sandbox without `failIfUnavailable`, and an advisory for a confined team with no sandbox merged. (rc.8)
- **The `confine-run.sh` launcher binds the control plane read-only,** including all of `.claude`, and
  self-binds its ancestors. It refuses symlinks and hard-linked protected files, and adds `--protect PATH`. On
  macOS its Seatbelt profile matches Linux. (rc.8, rc.9) #8
- **Launcher behaviour.** It uses a default-deny environment allowlist and runs on bash 3.2. A framework's
  control-plane entries are required only when its agents directory holds a build-log marker. New:
  `--protect-prompt-roots` (Linux) and `--coord-root PATH`. (rc.7, rc.8) #8, #78
- **Goose's macOS Seatbelt profile denies all of `.claude`,** as on Linux. (rc.8) #86
- **Goose Linux runner: shell injection fixed (HIGH).** Write roots were pasted raw into an operator script. They
  are now validated and shell-quoted. (rc.8)
- **Linux setup.** `scripts/install-sandbox-deps.sh` installs bwrap and socat and diagnoses AppArmor user
  namespaces; `test-sandbox-apparmor-userns.sh` probes a profile. (rc.8)

#### Codex under the launcher

- **Codex runs under agentteams' launcher.** This is how Codex holds under the switch (see "Frameworks under the
  switch"). `confined` and `exclusive` on Codex expand to `codex:sandbox`. Linux and macOS teams get
  `.codex/confined-run.example.sh`, which runs Codex inside `confine-run.sh`. (rc.9) #160
- **A per-project `CODEX_HOME`.** The launcher validates it and makes its config, hooks, prompt and plugin files
  read-only. (rc.9) #160
- **The Codex role gate.** Under the switch, Codex teams get `.agentteams/bin/codex-role-gate.py`, the pinned
  read-only file server and `.codex/hooks.json`. Calls from a spawned agent may use only `agentteams_readfs` read
  tools; every call is denied without `AGENTTEAMS_CODEX_CONFINED=1`. (rc.9) #160, #167
- **The runner checks the gate before starting.** It refuses unless the gate, server and hooks file match their
  pinned hashes, and refuses nested Codex config. A self-probe before every confined launch checks that keys are
  unreadable and the control plane is unwritable. (rc.9) #167

#### MCP servers and checks

- **A foundational MCP server catalogue.** First-party read-only `recall` and `gitread` servers are on by default
  wherever an MCP host feature is set; `github-read`, `github-write` (no merge, delete or fork) and `fetch` are
  opt-in. `agentteams --serve-mcp {recall,gitread}` runs them over stdio, confined to the project. Nothing is
  emitted under the switch. New brief fields: `mcp_catalog`, `mcp_catalog_exclude`, `pr_management`. (rc.9) #150
- **The MCP-need protocol.** Under the switch, teams decide from runner-ledger evidence whether an agent needs an
  MCP server, keep `references/mcp-needs.csv` (orchestrator-only), and route every capability-gap plan to the
  orchestrator. `--mcp-need-report` summarizes the evidence per agent. (rc.9)
- **`AR_WRITE_POLICY` audits the switch.** Under the switch it errors on any non-orchestrator agent that can
  write, on capability front-matter keys, on a shell on any non-orchestrator agent in a team with grants, on a
  granted agent that isn't rendered exactly, and on an orchestrator without "Applying Proposals". Every render
  warns `[WRITE-POLICY]` for adopted agents that fail it. (rc.9)
- **`--check-wiring` covers the switch.** It refuses any config that defines `agentteams_runner`, checks the
  required deny rules and that the sandbox is enabled, refuses loadable files in the backup tree, and flags a
  project-local `python3`. (rc.9, rc.10)
- **`--check-wiring` refuses unsafe status-channel setups:** a Read rule on `.agentteams-status/`,
  `allowUnsandboxedCommands`, and any Linux host for a team under the switch, until the runner's status channel
  is measured under bwrap. (rc.10) #203

### Frameworks

#### Claude

- **Skills are discoverable.** Skills are emitted as `.claude/skills/<name>/SKILL.md`, so they run as `/name`
  commands. Old flat files get a notice and are never deleted. `recall` leads with lexical search. (rc.7)
- **Bridge features** (`bridge:copilot-vscode-to-claude:*`): subagent stubs, example hooks, a cache-split
  `CLAUDE.md`, `/schedule` routines and a plan-to-TodoWrite skill. (rc.7)
- **The team builder launches.** Its file has front matter, and under the switch its canonical tools map to Read,
  Grep and Glob. (rc.7, rc.9)
- **Claude Code version tripwire.** `--check-wiring` notes when the installed `claude --version` has no recorded
  sandbox test pass. (rc.8)

#### Copilot (VS Code and CLI)

- **copilot-cli emits `.github/agents/*.agent.md`,** the VS Code format, instead of `.github/copilot/<slug>.md`.
  Old files are left as orphans. Sync refuses copilot-vscode and copilot-cli together; `--frameworks` narrows the
  set. Interop into copilot-cli keeps tools, model and roster. (rc.7)
- **`user-invokable` is renamed `user-invocable`;** deployed files migrate automatically. (rc.7)
- **Body size.** A body over 30,000 characters warns on native generation and is refused on interop.
  (rc.7, rc.10) #210
- **`--update` never blanks an orchestrator's `agents:` list,** and filtering handles flow and block lists.
  (rc.1, rc.7)

#### Goose (Beta)

- **Goose support.** `--framework goose` emits recipes, orchestrator `sub_recipes`, `AGENTS.md`, `.goosehints`
  and a team builder. Goose is a convert and bridge target. `.goosehints` no longer imports `AGENTS.md`, which
  Goose reads natively. (rc.7)
- **Grant-scoped recipes.** `goose_tool_scoping: "grant"` derives each recipe's extensions from its declared
  tools (verified on Goose 1.37.0). In rc.9 the switch forced grant mode; the switch itself is now refused on
  Goose, so set `goose_tool_scoping` directly. In legacy mode an empty extension list emits `extensions: []`.
  (rc.9, rc.10)
- **`agentteams_readfs`.** Read-only agents read through a read-only stdio file server shipped as package data
  and launched `python3 -I -S`. (rc.9)
- **Recipe checks.** `--post-audit` reports `AR_GOOSE_GRANT_EXCEEDED`, `AR_GOOSE_MARKER_MISSING`,
  `AR_GOOSE_READONLY_WRITE` and `AR_GOOSE_EXTENSIONS_FAIL_OPEN`. An operator MCP server's name never excuses a
  builtin. `--check` compares the installed Goose version with 1.37.0 (advisory). (rc.9)
- **Delegation is narrowed.** In grant mode the orchestrator recipe lists the exact `extensions` argument for
  each sub-recipe; unknown targets get only `agentteams_readfs`. This stays cooperative. #206
- **Merge keeps recipes intact.** `--update --merge` reconciles `sub_recipes` and `extensions` from the render,
  keeps enriched bodies and valid YAML, puts new sections inside `instructions: |`, and adds a Project-Specific
  Notes region. It removes hand-added `team-builder` entries from `sub_recipes`. (rc.8, rc.9, rc.10) #15, #189
- **Goose leaves other owners' `AGENTS.md` alone.** It no longer overwrites a root `AGENTS.md` it didn't write,
  and marks its own. (rc.10) #222
- **Runtime helpers.** `goose-run-resilient.py` resubmits dead turns; an OpenRouter route proxy and
  `--providers` preflight help with backend routing; a coordination MCP server and `coordination_write_roots`
  support cross-repo work. (rc.7, rc.8)

#### Codex (Beta)

- **Agents are `.codex/agents/*.toml`.** Tools and handoffs go into a fenced `codex_translation` block, which is
  template-authoritative. Tool docs are `.agents/skills/<slug>/SKILL.md`. Handoffs are pruned to the roster.
  (rc.8) #25
- **Every role gets an explicit `sandbox_mode`:** `workspace-write` only when it declares edit, otherwise
  `read-only`, which means no edit, execute or retrieval. Read-only agents get a "Reading on Codex" section (the
  role-gate version under the switch); shell agents get a "Secrets on Codex" section. (rc.9) #132
- **Codex bodies update.** Agent bodies are fenced, and `--update --merge --yes` retrofits legacy files. Every
  native agent has a Project-Specific Notes region, so `--update --overwrite` carries notes. Learned blocks sync
  to and from the TOML. (rc.9, rc.12)
- **A merge never writes an agent TOML that doesn't parse.** Every merged `.toml` is parsed before writing; a
  failure is refused, the file is left as it was and the run exits non-zero. (rc.11)
- **MCP.** `codex:mcp` writes read-only servers into a managed block of `.codex/config.toml` with
  `default_tools_approval_mode = "prompt"`. (rc.7, rc.8)
- **Shared `AGENTS.md`.** A foreign or symlinked root `AGENTS.md` is never written, even with `--overwrite`, and
  even before `.codex/agents` exists. (rc.8)
- **`AGENTS.md` under the switch.** A goose-owned `AGENTS.md` gets only a `codex_write_policy` fence; a
  hand-written one gets a paste-in file under `.codex/examples/` (not when it is a symlink). (rc.11)
- **Codex is an interop source,** with a lossless round trip. (rc.8)

#### AGENTS.md

- **`--framework agents-md`** emits a neutral repository-root `AGENTS.md` plus `.agents/`. It is generate-only, and
  it still overwrites any owner's `AGENTS.md`. (rc.7, rc.10)
- **`AGENTS.md` carries the constitutional rules** in a template-sourced `constitutional_rules_baseline` fence,
  with a heading for project extensions, and a `## Code Style` section from `style_rules`. (rc.7, rc.8) #16

#### Grok (Beta)

- **Grok Build adapter.** `--framework grok` renders `.grok/agents/<slug>.md` without default tools or MCP
  inheritance, co-owns `AGENTS.md` through a `grok_runtime` fence, and protects `.grok/`. It works with interop,
  `--absorb` and fleet; convert and bridge refuse it. (rc.10) #214, #216, #218
- **The Grok hook gate is advisory.** A pinned shim runs the unmodified gate and fails open. #216
- **No widening on Grok.** Scoped `tools:` entries and `MCPTool` are dropped, and denies are applied by leaving
  the tool out. A deny list that leaves no tool is refused. `--update --merge` names any `.grok/agents` file that
  still widens. #227, #228
- **Run an agent by path:** `grok --agent .grok/agents/<slug>.md`. Grok 1.0.52 can't spawn custom subagents. #227

#### Interop, sync, convert and bridge

- **`--interop-from` never widens a grant.** Each projected agent is re-read as its target reads it and checked
  against its source grant before anything is written. Pinned sync and `--convert-from` check every agent first.
  Front-matter values are written on one line and quoted. #210
- **`--interop-allow-cooperative SLUG[,SLUG…]`.** A narrow agent projected to Codex (outside its role gate) or
  agents-md is refused unless named here. Naming it needs a decision log and records a row. #210
- **Interop carries the switch into Claude.** `--interop-from DIR --framework claude --description BRIEF` applies
  the brief's `write_policy` and `mcp_grants`, so adopted bespoke agents get the runner. A non-Goose bridge
  orchestrator is narrowed, not refused. (rc.9) #174, #178
- **Interop into Goose under the switch is refused,** because the switch is refused on Goose. Without the switch,
  interop into Goose works; a pinned sync to Goose needs the pinned `agentteams_readfs` server. (rc.10) #210
- **Convert and bridge refuse a switched team.** `--convert-from` and bridge subagent stubs refuse a team under the
  switch and point to interop. (rc.9) #183
- **Interop-projected teams are visible.** Codex, Copilot and Goose projections write a team marker, so fleet,
  `--check` and the launcher see them; the marker authorizes nothing. (rc.9)
- **Skills.** `--interop-skills-only` imports skills only. Skill paths are contained, descriptions are kept and
  safely quoted, and slugs are validated. (rc.8) #71
- **Pinned sync.** `sync_pin` keeps several framework renders in step from one canonical source. Unchanged agents
  are left byte-identical, instruction files receive only fenced regions, and projections back up first.
  `--sync-init` and `--sync` take their root from `--project`. (rc.7, rc.8)
- **Canonical format.** The CAI round trip keeps capabilities, tool scopes, names and descriptions. A canonical
  directory can be a bridge source, `--framework generic` is a bridge-only target, and `--package-team` exports a
  portable zip. (rc.7)
- **Bridges.** `--bridge-merge` leaves project-owned entry files and native agent bodies alone and says so; the
  banner names the mode; the check report records a source digest. Bridged Goose teams document
  `agentteams.research`. (rc.7, rc.8)
- **Framework watch.** Every team ships `references/framework-watch.reference.md`. `--agent-check` and
  `--framework-freshness` report provider drift and stale renders; `--stale-check` reports `PIN_UNSYNCABLE`.
  (rc.1, rc.8)
- **Scoped Bash support is tracked.** The framework watch records whether upstream docs show that Claude honours
  `Bash(...)` scopes in subagents. Only a person may mark it supported. (rc.7)

### CLI and pipeline

#### Update, merge and fences

- **`--update` merges by default.** It preserves content outside fences; a full re-render needs
  `--update --overwrite`. Byte-identical files are not rewritten. (rc.1)
- **Fenced sections.** Merges update fenced regions only, and every persona has a Project-Specific Notes region.
  Legacy files are wrapped in a `content` fence on `--update --merge --yes` (`--no-add-fence-markers` opts out),
  and a stale `content` fence is migrated to named fences. (rc.1, rc.7, rc.10) #201
- **Fence tools.** `--add-fence-markers` adds fences to a legacy file. `--migrate` tags a snapshot and re-renders
  with fences; `--revert-migration` restores the snapshot. (rc.1)
- **Merges keep your edits.** Missing sections are inserted in position. Front matter gets a three-way merge
  (skills included), keeping both-side changes as reported conflicts. Hand-written handoffs are kept.
  (rc.7, rc.9, rc.10)
- **Merges say what they skip.** Unfenced drift is reported for unedited files, and `--update` writes
  `update.report.md`. (rc.7)
- **Shrink policies.** `--shrink-policy` takes `warn`, `halt`, `allow`, `preserve` (default) and `additive`.
  Security, constitutional and `codex_translation` fences are template-authoritative. A replaced body is saved as
  a `.lost` sidecar and reported. (rc.1, rc.2, rc.7, rc.8)
- **`--shrink-allow FILE:FENCE@DIGEST`** lifts `preserve` for one reviewed section, including files above the
  agents directory. (rc.9, rc.10)
- **User regions survive `--overwrite`.** Project-Specific Notes and Rules are carried into the new render;
  `--discard-user-regions` opts out. (rc.9)
- **Project Layout region.** A USER-EDITABLE Project Layout region in the instruction files overrides the
  generated layout. (rc.10) #193
- **Fence twins.** An unfenced copy of a fenced section is dropped only on proof. A row in
  `references/fence-twin-acks.csv` acknowledges a twin kept on purpose; fleet flags new acknowledgements once.
  (rc.10) #198, #199
- **Previews.** `--update --dry-run` previews every action, including front-matter capability changes; `--json`
  prints pure JSON. `--check` uses the same pipeline. (rc.1, rc.7)
- **`--reconcile-front-matter`** reports divergence from template front matter; `--reconcile-apply` applies it
  and prints `[CAPABILITY GRANT CHANGED]`. (rc.7, rc.10)
- **Learned agent notes.** `--sync-agent-docs` propagates `AGENTTEAMS-LEARNED` blocks between framework copies.
  It reports by default; blocks with findings are quarantined. Regeneration keeps them. (rc.9)
- **Safer targets.** A relative `--output` on a foreign git-tracked directory is refused
  (`--allow-foreign-output`). `.vscode/tasks.json` is never written outside the output tree. An in-sandbox
  `--update` refuses before writing. (rc.7, rc.8)
- **`--self` without `--output`** writes to the chosen framework's agents directory (`.claude/agents` for
  Claude). It refuses when that directory's signing switch is weaker than `.github/agents`. (rc.10)
- **`--update` on a bridge target needs `--materialize-native`.** (rc.7)

#### Adoption, fleet and backups

- **Adopting bespoke agents.** `--adopt-orphans` adds agent files to the roster. With `--update` it is
  append-only and needs a once-only operator Ed25519 decision (`adopt-orphans-merge`). (rc.7, rc.9)
- **`--adopt-only SLUG[,SLUG…]`** adopts a named subset and touches only the orchestrator. Adopted agents are
  routed in every orchestrator. (rc.9, rc.10) #209
- **`--overwrite` keeps adopted routing rows.** It refuses with `[ADOPTED-ROWS] blocked` rather than drop a row
  whose agent file still exists. (rc.9)
- **`--fleet DIR`** updates many workspaces in process (dry run by default). It skips its own output, worktrees
  and submodules, refreshes bridges from their recorded source, detects USER-EDITABLE regions correctly, and
  reports shrink grants and tracked volatile churn. (rc.7, rc.9, rc.10) #181
- **Backups.** Writes are atomic and backed up to `.agentteams-backups/` with a hash manifest. New:
  `--list-backups`, `--restore-backup`, `--verify-backup`, `--prune-backups`, `--backup-mirror` and `--no-backup`.
  (rc.1, rc.7)

#### Retrieval, research and indexes

- **Memory index.** `references/memory-index.json` is a BM25 index the navigator queries first. Results carry a
  confidence label. Paths are relative and local paths are redacted. (rc.1, rc.7)
- **Memory-index shards.** The index is written compactly, and above 20 MiB it is sharded into sha256-pinned
  parts. (rc.10) #219
- **Code index.** `--refresh-code-index` and `--query-code` search code and APIs from a gitignored cache that is
  never executed. A partition path can't leave the cache. (rc.7, rc.9)
- **`agentteams[research]`.** Search with a backend fallback chain, scholarly search (OpenAlex, Crossref, arXiv),
  a TTL cache and page fetch (400 KB cap). The `retrieval` tool token grants it to two agents. (rc.7)
- **Browser rendering.** `python -m agentteams.research browser URL` renders a page with Playwright
  (`agentteams[browser]` plus `playwright install chromium`). (rc.7, rc.10)
- **News as perspective.** `agentteams.research.news` tags news sources and attributes their claims as
  perspective, with published dates. (rc.7)
- **Source allowlist presets.** `SOFTWARE_CONFIG`, `RESEARCH_CONFIG`, `DATA_CONFIG` and
  `config_for_project_type()` give per-archetype source allowlists. `DEFAULT_CONFIG` is unchanged. (rc.7)
- **Research-capability advisory.** The manifest advises, and does not enable, `research_verification` when a
  team could use it. (rc.7)
- **Every team ships an external-retrieval quality gate:** externally sourced summaries are reviewed before
  use. (rc.7)

#### Branch lifecycle

- **`--branch-inventory`, `--branch-cleanup` and `--branch-post-merge`.** Inventory classifies branches and writes a
  deletion plan. Cleanup needs a once-only `@security` PASS bound to the plan's hash; post-merge deletion needs an
  operator Ed25519 grant. Deletions are logged to a hash-chained ledger, and the gate asks before either one
  applies (see "The constitutional gate hook"). (rc.9) #102

#### Checks, audits and records

- **`--verify-integrity` passes on a clean checkout of a Claude team.** The gate's machine-local interpreter pin is
  gitignored, so it is reported as `LOCAL` when absent, not `MISSING`. Every other absence is still `MISSING`, and
  `--check-wiring` still checks the interpreter. (rc.14, found in baseAgent) #242
- **New checks:** `--check-budget`, `--check-rank` and `--check-rank-strict`, `--check-wiring`, `--stale-check`
  and `--stale-remediate`, `--capture-baseline` and `--check-baseline`, a living-document check and a dual
  descriptor advisory. `--post-audit` and the orphan advisory work for every framework. (rc.1, rc.3, rc.7, rc.9)
- **The build log records which code ran.** `build-log.json` records `agentteams_source`, and `--version` prints
  it. (rc.8, rc.9) #40, #106
- **Behavioural drift detection.** `behavioral_drift.detect_behavioral_drift` compares a run's trajectory with
  the team's eval suite and reports divergence. (rc.1)
- **`python -m agentteams.session_scan`** collects known issues, pending or blocked plan steps and git status
  anomalies. (rc.7)
- **A man page,** `agentteams.1`, is generated from the CLI. (rc.1)

#### Generation and templates

- **Tools are documents, not agents.** A tool emits a reference document (Copilot) or a skill (Claude) and is no
  longer on the roster. (rc.7)
- **The default model is Claude Opus 4.8** for every generated agent. `--cost-routing` tiers are unchanged.
  (rc.7)
- **Generated references and records:** an eval suite, a delivery receipt, a team graph and a model-routing
  contract (`--cost-routing`, off by default). (rc.1)
- **Eval adapters.** `agentteams/eval_adapters/` turns the eval suite into Inspect AI task source or an OpenAI
  Evals definition, without importing either framework. (rc.1)
- **Templates use the update features.** The orchestrator gains a behavioural-verification workflow that reads the
  eval suite. The conflict auditor checks typed handoffs. The adversarial and work-summarizer agents query the
  memory index first. The agent updater documents dry-run JSON, cost routing, receipts and backups. (rc.1)
- **Workflow templates:** governance workflow updates, work-summary capture and backfill, the Post-Deliverable
  Retrospective, a post-merge CI check and parallel plan waves. (rc.1, rc.7)
- **Code-hygiene and review rules:** new code-hygiene rules on exception handling, AI bad habits, script-first
  output and dispatch tables; technical-validator rules get their own numbering; quality and adversarial checks
  cover claims that depend on an unstated time, place or text version; read-only roles default to terse output.
  (rc.3, rc.7, rc.9) #180
- **A Unix-philosophy reference** maps the code-hygiene rules to Unix principles. (rc.1)
- **Post-production auditor** template and closure-gate schemas. (rc.1)
- **Opt-in agents:** `@pr-manager`, `@pr-notifier` and `@pr-reminder` (never merge), and `agentteams-updater`
  (proposal-only). (rc.7, rc.9)
- **`--enrich` and `--auto-correct`.** `--enrich` fills default template elements from project context and exports
  `defaults-audit.csv`. `--auto-correct` runs the `copilot` CLI to fix audit findings, then re-audits. (rc.1)
- **`--target-host-features`** takes `<namespace>:<feature>` tokens that turn on opt-in emission paths. Default
  output is unchanged. (rc.7)
- **MCP server auto-detection** is added but inert: default emission is unchanged. (rc.7)
- **Brief handling:** `{MANUAL:*}` tokens become N/A outside `--self`; archetype matching uses word boundaries;
  the module-doc pair needs packaging or docs-site signals; tool docs can follow a pinned version; scripts and
  destructive commands are never a default task; schema fixes. (rc.1, rc.7, rc.9, rc.10) #208
- **Ingest fallbacks.** A Markdown brief's `project_goal` comes from an overview heading or the first paragraph.
  With `doc_site_config_file` declared, reference and style paths are inferred from `docs/` or `docs_src/`.
  (rc.1, rc.7)
- **`devDependencies` no longer get a reference document each;** they are categorised `other`. (rc.7)
- **The `build-team` command is deprecated.** It still works through 1.x with a warning; use `agentteams`. (rc.1)

### Release and installation

- **Releases.** `v1.0.0` is a signed tag on `release/1.0`; `SECURITY.md` lists the release signing keys by tag.
  (rc.9, rc.10)
- **Install hints pin a real tag.** A release or pre-release maps to its own tag. Dev and local builds pin
  `LAST_RELEASE_TAG`, which a release PR updates with `pyproject.toml`. (rc.8, rc.9, rc.11)
- **agentteams itself is never installed from PyPI.** Every emitted install command uses the git source,
  `pip install "agentteams[<extra>] @ git+https://github.com/jlcatonjr/agentteams.git"`. Commands generated at
  runtime pin the installed tag (`…agentteams.git@<tag>`). (rc.8)
- **Dependencies.** The one runtime dependency is `jsonschema>=4.20`, installed from PyPI. Optional extras:
  `research`, `signing` (`cryptography==50.0.0`), `browser` and `test`. (rc.1, rc.2, rc.7, rc.8)
- **Packaging.** The wheel ships the JSON schemas, the Goose helper scripts and the read-only file server, and
  leaves out scratch directories. `__version__` comes from package metadata. (rc.1, rc.7, rc.9, rc.10)
- **The `signing` extra pins `cryptography==50.0.0`** (CVE-2026-69247). (rc.8)
- **Environment scripts:** `scripts/verify-env.py` (Python 3.11 or later, git 2.23 or later) and
  `scripts/install-agent-doc-sync.sh` (systemd units for learned-note sync). (rc.1, rc.9)

### Docs

- **`STABILITY.md`** lists the SemVer surface, a status table for all seven frameworks and the per-framework 1.0
  status of `write_policy`. `SECURITY.md` gives disclosure, the threat model and the signing keys. (rc.1, rc.11)
- **New:** `docs_src/known-limitations.md`; "Upgrading to 1.0" in `docs_src/migration-guide.md`; Grok privileges;
  the confinement and `proposal_run` API pages; runtime-governance guides. (rc.7, rc.9, rc.10, rc.11) #179
- **Corrected:** the claim that interop into Goose is unsupported (it is supported without the switch); the Goose
  privileges profile; the threat-model disclosure of laundering; the waiver example's placeholder key. The CLI
  reference lists every flag. (rc.7, rc.9, rc.10, rc.11, rc.12)
- **Release checkouts and signing.** The migration guide notes that a release checkout records its pin only when
  pip-installed: a source-path install (`.pth` or `PYTHONPATH`) reports `0.0.0+local` and records no pin.
  `SECURITY.md` explains why a signer installed under `$HOME` can be refused when a repository has no
  `.claude/settings.json`: install it outside `$HOME`, for example under `/opt`. (rc.14) #237
- **Architecture graph** (`references/architecture-graph.md` and its SVG) regenerated for the 1.0 module layout.
  (rc.14) #236

### Breaking changes and migration

This section is for anyone upgrading from 0.1.0 or from any release candidate. The steps, with commands, are in
`docs_src/migration-guide.md`, "Upgrading to 1.0". Run them from the 1.0 install, before re-rendering.

- **Check HALTs and the chain first.** Run `agentteams --verify-integrity`. Its report lists each unretracted HALT
  that the token rule now makes blocking, with the id to retract it by. Retract what no longer applies. (Step 1)
  #224, #225
- **Migrate the decision log.** Run `--migrate-decision-log --yes` for the signing columns, row padding and
  `text_digest`. Re-issue any revoked retraction as a signed row. (Step 2)
- **Re-sign rows** signed before rc.9 that have a non-empty `scope` or `effect_*` value, and every later row in a
  chained log. Don't clear the cells instead. (rc.9)
- **Replace weak HMAC keys.** Generate a 32-byte secret (`python -c 'import secrets; print(secrets.token_hex(32))'`),
  re-sign outstanding decisions, waivers, directives and grants, and write a new ledger key (mode 0600) before
  issuing new nonces. (rc.12)
- **Re-render, then check the gate.** Re-render, or copy the template hook into `.claude/hooks/` and
  `.github/hooks/`, then run `--check-wiring`. (Steps 3 and 4)
- **`research browser --screenshot` is removed.** Use the `browser_screenshot()` library call instead.
  (Step 5) #224
- **Scripted signing** needs `--confirm-review-sha256 <digest>`. Signing from a checkout is refused; sign from a
  trusted install, or pass `--allow-checkout-signing`. The key-provisioning helper needs `--team-dir`. (rc.8)
- **Move the signing key.** The operator runs `provision-operator-signing-key.sh --migrate` to move it to
  `~/.config/agentteams/keys/`, updates any exported keyfile path, and re-merges the sandbox block. (rc.8)
- **Grants.** HMAC write grants no longer widen a sandbox; re-issue them with `--sign-grant`. Create
  `references/security-approvers.txt` before issuing or verifying grants. (rc.7, rc.8)
- **`privilege_profile` defaults to `confined`.** To opt out, set `"privilege_profile": "cooperative"`. (rc.8)
- **Decision signing is on.** To opt out, set `enforce_decision_signing: false` in the brief. (rc.7)
- **`--update` merges.** Scripts that relied on a full re-render must pass `--update --overwrite`, which needs an
  `overwrite` clearance. (rc.1)
- **Merges wrap legacy files in fences.** To opt out, pass `--no-add-fence-markers`. (rc.7)
- **The default model is Opus 4.8.** A `model:` value you set by hand is kept: a merge reports template model
  changes but doesn't apply them. (rc.7)
- **Sandboxes.** Re-merge the Claude `sandbox` and `permissions` blocks (including `failIfUnavailable`) after
  updating. Dangerous write roots are refused; add a new external root with `--accept-write-root PATH`. (rc.8)
- **Profiles and hosts.** An unknown `privilege_profile` fails the build; fix the spelling. On native Windows and
  other platforms without a launcher, a confined non-Claude build exits non-zero; pass
  `--allow-unenforced-confinement` or use `"cooperative"`. (rc.7)
- **macOS launcher.** It refuses a team that is missing a control-plane entry; run `agentteams --update`. (rc.9)
- **The switch.** It needs an explicit `confined` or `exclusive` profile, and is refused on Goose, Copilot and
  agents-md. Interop under the switch targets Claude only. (rc.9, rc.10)
- **The confined-programs file.** Reinstall it once with `--install-confined` and `--confirm-review-sha256`, so it
  records `gate_argv_sha256`. (rc.9)
- **The runner.** Clients without a `reply_key` are refused: upgrade every agentteams install that talks to the
  runner to 1.0, then restart `--serve-requests`. (rc.10)
- **Interop with existing agents** under the switch needs `--overwrite`. (rc.9)
- **Framework output moved.** Codex agents are `.codex/agents/*.toml`; copilot-cli agents are
  `.github/agents/*.agent.md`; Claude skills are `<name>/SKILL.md`; tools are documents. Remove the old files by
  hand. (rc.7, rc.8)
- **Codex roles.** Command roles that don't declare edit are now `read-only`. Declare edit for a role that must
  write. (rc.9)
- **Goose and `AGENTS.md`.** Goose no longer overwrites an `AGENTS.md` it didn't write. To hand the file to Goose,
  delete it and re-render. (rc.10)
- **Goose and `.goosehints`.** `.goosehints` no longer imports `AGENTS.md`; Goose reads it natively, so no action
  is needed. (rc.7)
- **Grok scoped entries are dropped.** Run `--reconcile-front-matter --reconcile-apply`, or fix the files that
  `--update --merge` names. (rc.10)
- **Widening interop is refused.** Name each agent whose cooperative grant you accept in
  `--interop-allow-cooperative`. Interop also refuses a Copilot body over 30,000 characters; shorten the body
  first. (rc.10)
- **`--from-migrate` is removed.** Use `--migrate`, which sets its gate exemption internally. (rc.1)
- **`--self` writes elsewhere.** Without `--output`, it writes to the chosen framework's agents directory
  (`.claude/agents`, `.goose/recipes`, `.codex/agents`, `.agents`). Copilot (the default) still uses
  `.github/agents`. The old location was a bug that put non-Copilot agents in the Copilot directory in the wrong
  format, so do not recreate it with `--output`; remove any such stray files from `.github/agents`. (rc.10)
- **CLI.** `--update` on a bridge needs `--materialize-native`; `--sync-init` and `--sync` refuse `--output`
  (pass `--project`). Hand edits inside `codex_translation` are overwritten; move them to Project-Specific Notes.
  (rc.7, rc.8)
- **`scripts/resolve_fence_collisions.py --trust-provenance` exits 2.** Use `agentteams --update --merge`, which
  drops a fence twin only on proof. (rc.10)
- **Memory index.** Once one writer produces a sharded index, keep every writer on 1.0. (rc.10) #219
- **Rolling back:** see "Rollback A: turning `write_policy` off" and "Rollback B: reverting to rc.9" in the
  migration guide.

### Known limitations

These ship with 1.0. Those from rc.10 onward are described, with workarounds, in `docs_src/known-limitations.md`;
the rest are stated here.

- Clearances bind by id prefix; name them `<action>-<instance>` and use the `scope` column. A CONDITIONAL PASS
  needs `conditions_verified` to be exactly `verified`. A family retraction clears every prefix-matched HALT.
  (rc.10)
- Logs signed before 1.0 may verify through a legacy prefix; deletions inside it, and rows cut from the tail,
  are not detected. (rc.10)
- Grants are cooperative, not enforced, on Goose delegation, Codex outside its role gate, agents-md and Grok.
  (rc.10)
- Claude under the switch is refused on Linux. (rc.10)
- Claude Code's built-in general-purpose subagent holds Bash. (rc.10)
- The research cache still writes under `references/research-cache`. (rc.10)
- Codex: a merge doesn't narrow `sandbox_mode`; a project's own `.codex/hooks.json` keeps the role gate out; new
  teams start a legacy six-column decision log; the role gate has no codex-cli version pin. (rc.11, rc.12)
- A signed `overwrite` row can't cover a render's `CLAUDE.md` write. (rc.11)
- In an unconfined session, anything that can write the verify-key store can plant a key. (rc.11)
- Copilot: the orchestrator and security agents exceed GitHub's custom-agent body size. (rc.12)
- Repository scanning with `--project` can change the roster. (rc.12)
- macOS: only git gets a helper directory for an xcrun launcher, and an unresolved launcher alone warns but is
  not refused. (rc.13)
- Adopted agents can't be put under orchestrator-only on Codex, so a team with adopted agents keeps its Codex
  surface frozen for 1.0. (rc.14) #237
- `AGENTS.md` is not classed as a governance trust root: a clearance targeting it is classed by its other effects.
  Review such rows as you would a `CLAUDE.md` one. (rc.14) #237
- Revocation: proposals an agent staged before its MCP grant was revoked can still be applied with the
  orchestrator's approval. Reject them by hand. (rc.14)
- The prompt-injection evaluation (Experiment 1) runs after 1.0, under its approved test plan; it is not yet
  evidence for this release. Its findings ship as `v1.0.N` fixes or 1.1 changes. (1.0.0, release notes only)
- `.agentteams-backups/` is not added to `.gitignore` for you. Add it, and `git rm --cached -r` it if it was
  already committed. (rc.1)
- `--migrate` regenerates every agent file, so content added after generation is lost; recover it from the
  `pre-fencing-snapshot` tag. Use `--update --merge` for routine updates. (rc.1)
- `jsonschema` is a required runtime dependency, the one exception to the base install being stdlib-only. (rc.2)
- On `--update --merge` under the default `--shrink-policy preserve`, a fenced section whose enriched body would
  shrink keeps its old body, so a brief change may not reach it; check the shrink notices. (rc.7)
- The Tone and Style section names a fixed set of agents, some of which your team may not have. (rc.7)
- A confined command can be reported as failed (`undeclared_writes`) when the generated Claude hook writes
  `.claude/hook-notices/<date>.log` while the command runs. It is a false failure, never a false pass; re-run the
  command. Fixed in 1.0.1. (1.0.0, release notes only; M6-F7)

## [0.1.0] - 2026-04-15

### Added

- **Reference: Unix Philosophy Mapping for Code Hygiene Rules** — added `agentteams/templates/domain/unix-philosophy-mapping.template.md` and integrated into build pipeline. Each generated team includes `references/unix-philosophy-mapping.reference.md` mapping rules (CH-01 through CH-23) to Unix design principles. Three-tier classification: Tier 1 (foundational), Tier 2 (aligned), Tier 3 (project-specific). See audit report `tmp/by-week/2026-W20/unix-philosophy-mapping-audit-revisions.md`.

- `ingest` module: load project descriptions from `.json` or `.md` briefs; scan existing project directories to supplement missing fields
- `analyze` module: classify project type, select agent archetypes, detect tool agents, build team manifest
- `render` module: resolve auto and manual placeholders in agent templates; compute template hashes for drift detection
- `emit` module: write rendered agent files to disk with dry-run and overwrite-protection support
- `drift` module: detect content drift (template hash comparison) and structural drift (team composition changes) against build-log
- `scan` module: proactive security scan for PII paths, credentials, and unresolved placeholders
- `audit` module: post-generation static audit plus optional AI-powered review via `copilot` CLI
- `remediate` module: auto-correction support via standalone Copilot CLI after audit findings
- `graph` module: directed graph inference for agent team topology; outputs Mermaid, DOT, JSON, and Markdown
- `frameworks` package: `copilot-vscode`, `copilot-cli`, and `claude` framework adapters
- `build_team.py` CLI: 16-flag command-line interface wiring all pipeline stages
- Template library at the 0.1.0 release: 9 universal governance templates, 9+ domain archetype templates, 3 builder templates, 6 workstream expert-pattern templates
- JSON schemas: `project-description.schema.json` and `team-manifest.schema.json`
- Example project briefs: research, software, and data-pipeline projects
- `--self` mode: self-maintenance of the module's own agent team
- `--post-audit` mode: static + AI-powered conflict and presupposition review
- `--update` / `--prune` mode: incremental re-rendering with manual value preservation
