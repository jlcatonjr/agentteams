# Known Limitations in agentteams 1.0

The 1.0 release triage (@security and @adversarial, approved by the operator) classed the items below as
**documented limitations**, not release blockers. Each entry says what the limitation is, who it affects, what to
do about it, and where it is tracked. A fix may land in a 1.0.x or 1.1 release, and may change behaviour when it
does (see the [stability policy](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md)).

"Remediation log row N" refers to line N of `references/agentteams-remediation-log.csv` (the header is line 1).

---

## Orchestrator-only writes (`write_policy`) and the runner

### Turning `write_policy` off is not a clean rollback

- **What:** removing the switch from the brief and re-rendering (`--update --merge`, then
  `--reconcile-front-matter --reconcile-apply`) restores tools, Goose extensions and the settings example. It
  leaves three things behind:
  - **(R1)** each granted agent's `mcpServers` `agentteams_runner` block, because reconcile never removes a key
    the template no longer declares;
  - **(R2)** orphaned `write_policy` fences (`<!-- AGENTTEAMS:BEGIN write_policy v=1 -->` …
    `<!-- AGENTTEAMS:END write_policy -->`) in agent files. The merge keeps orphaned fenced sections by design, so
    agents are told they can't write while they hold `Write` again. The pilot drill found about 44 such files.
  - **(R3)** `.agentteams/bin/agentteams-runner-mcp.py` and `.agentteams-status/`. The runner also leaves its
    run-time queue state: `.agentteams-queue/`, `.agentteams/queue-results/`, `.agentteams/queue-claimed/`,
    `.agentteams/runner.lock` and `.agentteams/runner.heartbeat`.
- **Who:** any team that turns the switch off after running with it.
- **Workaround:** the manual cleanup in the [Migration Guide](migration-guide.md#rollback-a-turning-write_policy-off),
  "Rollback A". Do not delete `.agentteams/` as a whole: it also holds state the team still uses.
- **Tracking:** remediation log row 344.

### Headless `claude -p`: a granted agent's runner server is not connected

- **What:** in a headless `claude -p` session (Claude Code 2.1.296), a granted subagent's inline `mcpServers`
  `agentteams_runner` is not connected. The agent has only its non-MCP tools, so it cannot write at all. The
  orchestrator's `--apply-proposal` fallback is correctly refused for granted agents. An interactive session
  (2.1.246) connected the server. The cause is unconfirmed: headless mode or the Claude Code version.
- **Who:** Claude teams under the switch with `mcp_grants`, run headless (CI, scripted runs, evaluation harnesses).
- **Workaround:** run granted agents in an interactive session. Treat a granted agent in a headless run as
  unable to write.
- **Tracking:** remediation log row 359.

### Grants are cooperative, not enforced, on Goose delegation, Codex outside its role gate, agents-md and Grok

- **What:** these surfaces do not enforce an agent's declared tools.
  - **Goose:** a `delegate` runs with the orchestrator's extensions, so a recipe's grant binds only on a direct
    `goose run --recipe`. Render refuses `write_policy` on Goose for this reason. The goose bridge stubs also
    still declare the whole `developer` extension.
  - **Codex:** `sandbox_mode` and the tool list are self-imposed, except under the `write_policy` role gate.
  - **agents-md** (the framework-neutral `AGENTS.md`): the surface has no grant channel.
  - **Grok:** grants are declared only. No live test has yet shown a read-only agent blocked from editing.
- **Who:** teams that render or project read-only agents onto these surfaces.
- **Mitigation:**
  - `--interop-from` refuses to project a narrower-than-default agent onto Codex (outside the role gate), Grok or
    agents-md unless it is named in `--interop-allow-cooperative`. Each such opt-in is recorded in the destination
    team's decision log, and the agent carries a "grant is cooperative on this surface" banner.
  - On Goose, run read-only agents directly.
  - On Codex, use the launcher, `.codex/confined-run.example.sh`.
- **Tracking:** `STABILITY.md` section 6. Remediation log rows 386 and 396 (Goose) and 432 (Grok).

### Claude under the switch is refused on Linux

- **What:** `--check-wiring` refuses a Claude team under the switch on Linux. The runner's `.agentteams-status/`
  channel has not been measured under bwrap; a read-denied queue might be masked rather than refused.
- **Who:** Linux operators of Claude teams under the switch.
- **Workaround:** run the switch on macOS. There is none on Linux for 1.0.
- **Tracking:** CHANGELOG `[1.0.0-rc.10]`, "the sandboxed Claude orchestrator reaches the runner". baseAgent
  request #4, targeted at 1.1.

### Claude Code's built-in general-purpose subagent holds Bash

- **What:** if the orchestrator dispatches Claude Code's built-in general-purpose subagent, that subagent has
  Bash, and therefore the orchestrator's channel to the runner. agentteams does not render that subagent, so
  neither its audit nor a verified permission rule can narrow it.
- **Who:** Claude teams under the switch.
- **Mitigation:** dispatch only team agents.
- **Tracking:** CHANGELOG `[1.0.0-rc.10]`, the same entry, "Residual (documented)".

### macOS: only git's helper directory is added for an xcrun launcher

- **What:** when a confined program's `exec` names an Apple xcrun launcher in `/usr/bin` (`git`, `make`,
  `clang`), the runner's Seatbelt profile also allows the real tool (by exact path) and, for git only, its
  `--exec-path`. Another launched tool that runs helpers, or sibling tools, outside that is still denied, and
  its confined calls fail with exit 71. If xcrun is missing or fails, or a path isn't root-owned, nothing is
  added; the runner and `--check-wiring` warn. The run is refused only when a write root also covers a package
  store. Otherwise a tool that misreads exit 71 can still act on it inside its write roots (mathAgents M6:
  `lake` deleted `.lake/packages/mathlib`).
- **Who:** macOS operators whose confined programs call developer tools through `/usr/bin`.
- **Workaround:** add the helper directory to `exec` yourself, and keep write roots off package stores
  (`.lake/packages`, `node_modules`, `.venv`, ...). The runner warns about those, but does not refuse them.
- **Tracking:** CHANGELOG `[Unreleased]`, "xcrun launchers in the exec allowlist reach their real tool".

## Decision log and the security gate

### Clearances bind by id prefix; prose scope is not enforced

- **What:** a clearance binds to the newest decision row whose id starts with the action id. A clearance named
  differently never binds. An older, unconsumed single-use row can clear instead of the one you intended, so a
  stale clearance can be replayed when its scope lives only in prose.
- **Who:** every signing-governed team that records clearances by hand.
- **Workaround:** name clearances `<action>-<instance>`, and restrict them with the `scope` column.
- **Tracking:** CHANGELOG `[1.0.0-rc.10]`, "a HALT blocks every action named in its id", "Documented known
  limitations". A near-miss warning is planned for 1.1.

### A CONDITIONAL PASS needs `conditions_verified` to be exactly `verified`

- **What:** any other value, including `verified` followed by notes, does not count.
- **Who:** anyone recording CONDITIONAL PASS rows.
- **Workaround:** write exactly `verified`, and put the evidence in the `evidence` column.
- **Tracking:** the same CHANGELOG entry. A `verified:<evidence>` form is planned for 1.1.

### A family retraction clears every prefix-matched HALT of its action

- **What:** a `HALT-RETRACTED` row such as `overwrite-001` clears every HALT that matches the action `overwrite`
  by prefix, `overwrite-999` included.
  - This is unchanged from rc.9, and disclosed.
  - A HALT matched only by token (new in 1.0) is retracted only by its exact id.
- **Who:** teams with more than one open HALT for the same action.
- **Workaround:** before recording a retraction, list the open HALTs (`--verify-integrity` reports them).
  Retract by the exact HALT id where a family retraction would clear more than you mean to.
- **Tracking:** the same CHANGELOG entry, "Disclosed, unchanged".

### Logs signed before 1.0 may verify through a legacy prefix

- **What:** before 1.0, `--sign-decision` never filled `prev_digest` (defect 338). When signing is active, 1.0
  accepts a leading run of rows with empty links, so those logs work again without re-signing. Within that prefix:
  - deletions cannot be detected, HALT rows included;
  - the chain is unkeyed, so rows cut from the tail of any log are not detected either.
- **Who:** teams that signed decisions on rc.9 or earlier.
- **Mitigation:** `--verify-integrity` reports the prefix length and every HALT inside it. Confirm those rows are
  still present as recorded. No re-chaining tool ships in 1.0.
- **Tracking:** remediation log row 338 (fixed by #224). CHANGELOG `[1.0.0-rc.10]`, "1.0 must-fix set",
  "Disclosure".

### The Rule 15(d) aggregate cap is not consulted for operator-signed relaxing decisions

- **What:** `exception_registry.assert_within_aggregate_cap` runs only on the adopt-merge path. Other
  Ed25519-signed relaxing decisions (cross-repo actions, grants) are bounded only by their own expiry.
- **Who:** operators of signing-governed workspaces who sign relaxing decisions.
- **Mitigation:** give every signed relaxing decision a short expiry, and review the decision log's open relaxing
  rows at each release or fleet round. `--audit-exceptions` covers only `references/exception-registry.json`, so it
  does not see these rows.
- **Tracking:** remediation log row 337.

### A full render can't be covered by a signed `overwrite` row, because it writes CLAUDE.md

- **What:** every render writes the managed block of `.claude/CLAUDE.md`, and the effect classifier treats
  CLAUDE.md as a governance trust root. In a workspace where signing is active, a signed `overwrite` row whose effect
  includes that write is refused as non-eligible, so no honest signed row covers a full render.
- **Who:** teams with decision signing active that re-render a Claude team.
- **Workaround:** discard the render's change to `.claude/CLAUDE.md` and have the operator apply the managed-block
  diff by hand. Sign the `overwrite` row for the remaining files.
- **Tracking:** 1.0 known limitation (found by mathAgents, 2026-10-16). A render mode that leaves CLAUDE.md to the
  operator and prints the diff is planned for 1.0.x.

### Installing an existing operator key into a second team needs a manual step

- **What:** `references/authorized-verify-keys/provision-operator-signing-key.sh` refuses when the private key
  already exists ("rotate with a new key-id"). So it cannot install an existing operator key's public half into
  another team's `references/authorized-verify-keys/`, and Ed25519-signed decisions cannot be verified in that team.
- **Who:** operators adding a team (or a second framework surface) under an existing key.
- **Workaround:** from an operator shell outside every agent session, derive the public key from the existing
  private key:

  ```
  openssl pkey -in <private key> -pubout -out <team dir>/references/authorized-verify-keys/<key-id>.pub.pem
  ```

  Do not overwrite an existing `.pub.pem`. Treat the step as a trust-root write, cleared by @security.
- **Tracking:** remediation log row 398. The proposed fix is an `--add-team-dir` helper mode.

### The orchestrator's text misstates which allowlist source wins

- **What:** the rendered orchestrator "Applying Proposals" text implies that the operator's `confined_programs`
  file wins when both it and the brief's block exist. In fact both at once are refused (`write_policy.py`,
  `proposal_policy.py`).
- **Who:** teams under the switch that configure confined programs.
- **Workaround:** use one source, either the operator file or the brief block, not both.
- **Tracking:** remediation log row 422.

## The constitutional gate hook and network egress

### The gate does not see WebFetch, MCP tools, Read or Task

- **What:** the hook matcher is `Write|Edit|NotebookEdit|MultiEdit|Bash`. Calls where injected content enters
  (Read, WebFetch, Task, MCP tools) never reach it. The same goes for markdown image and link URLs rendered into
  chat output, which the client fetches with no tool call. Bash findings route to `ask`, not `deny`.
  - By default the hook keeps no per-case record.
  - The opt-in gate decision log (`AGENTTEAMS_GATE_LOG`, an absolute path outside the project and any git work
    tree) records each invocation. This closes the "no record" part only: the log is evidence, not the boundary.
- **Who:** all Claude teams with the gate hook installed.
- **Mitigation:** deny or ask WebFetch and unneeded MCP servers in your settings' permission rules. Confine egress
  (next entry).
- **Tracking:** remediation log rows 388 and 413. Threat model v3, section 2a.

### The project sandbox block does not confine network egress

- **What:**
  - The emitted Claude sandbox block carries no network deny, so egress is unconfined by default (row 387).
  - Claude Code ignores `sandbox.network.strictAllowlist` in project settings; it is honoured only in user or
    managed settings (row 410).
  - Under a user-level strict allowlist, the project's own `allowedDomains` are inert (row 414).
  - So a repository cannot confine, or grant itself, egress.
- **Who:** every team that relies on the project settings for network confinement.
- **Mitigation:** set the allowlist in user or managed settings, or wrap the session in
  `sandbox/confine-run.sh --egress deny` (or `proxy`). `--egress proxy` on Linux needs a network namespace that
  `egress-netns-wiring.sh` creates, and that script is not in the repository.
- **Tracking:** remediation log rows 387, 410 and 414.

## Codex (`--framework codex`)

### `--update --merge` does not narrow an existing team's `sandbox_mode`

- **What:** turning `write_policy: orchestrator-only` on for an existing Codex team and running `--update --merge`
  leaves the specialists' `sandbox_mode = "workspace-write"` in place. Only a fresh build or `--update --overwrite`
  (with an `overwrite` clearance) rewrites it. Agent files that agentteams did not generate are never touched, and
  fail the write-policy audit.
- **Who:** Codex teams adopting the switch on an existing render.
- **Workaround:** run `--update --overwrite` with a clearance, or rebuild. The merge prints this warning, and
  `[WRITE-POLICY]` names each file it did not generate. Under the launcher the role gate denies every spawned
  agent's writes whatever its `sandbox_mode`, so enforcement still holds.
- **Tracking:** 1.0 known limitation. A three-way merge of `sandbox_mode` is planned for 1.0.x.

### A project's own `.codex/hooks.json` keeps the role gate out

- **What:** the Codex role gate is installed only when agentteams owns `.codex/hooks.json`. If the project has its
  own hooks file there, the render leaves it alone and says so, and `.codex/confined-run.example.sh` then refuses
  to start, because the file is not the pinned copy. This fails closed.
- **Who:** Codex projects with their own project hooks.
- **Workaround:** move your own hooks to `CODEX_HOME/hooks.json`, then re-render.
- **Tracking:** 1.0 known limitation. Merging the gate entry into an existing file is planned for 1.0.x.

### New teams start a legacy six-column decision log

- **What:** a fresh render seeds `references/security-decisions.log.csv` with the older six-column header, on
  every framework. A signed row (for example a `#210` or Rule 15 row) needs the current schema.
- **Who:** teams that will sign decisions.
- **Workaround:** run `--migrate-decision-log` on the team before the first signed row (see
  [Upgrading to 1.0, Step 2](migration-guide.md#step-2-upgrade-or-re-pad-a-legacy-log-migrate-decision-log)).
- **Tracking:** 1.0 known limitation. Seeding the current header is planned for 1.0.x.

### The Codex role gate is not pinned to a codex-cli version

- **What:** the generated role gate reads Codex's hook input, including fields Codex does not document. A future
  codex-cli release could change those fields, and nothing in the gate or the launcher detects that.
- **Who:** Codex teams under `write_policy: orchestrator-only`.
- **Workaround:** pin codex-cli yourself, and re-run your launcher checks after any Codex upgrade.
- **Tracking:** 1.0 known limitation. A version pin that fails closed on an untested codex-cli is planned for 1.0.x
  (found by baseAgent).

## Copilot (`--framework copilot-vscode`)

### The orchestrator and security agents exceed GitHub's custom-agent size limit

- **What:** the generated copilot-vscode `orchestrator` and `security` agent bodies can exceed GitHub's 30,000-character
  custom-agent limit (about 70,000 and 49,000 characters on one project), so Copilot truncates or rejects them.
- **Who:** copilot-vscode teams. Claude, Codex, Goose and Grok are unaffected.
- **Workaround:** rely on the Claude or Codex surface for those two roles, or trim their Project-Specific Notes.
- **Tracking:** 1.0 known limitation. Splitting long sections into references, with an emit-time size check, is
  planned for 1.0.x (found by masterTeacher).

## Rendering

### Repository scanning can add `retrieval-integrator` to the roster

- **What:** every surface shares one roster. `retrieval-integrator` is included when `retrieval_integration` is
  set, either in the brief or by a scan of the repository. The scan runs whenever `--project` is passed without
  `--no-scan`. Rendering some surfaces with `--project` and others without can therefore give them different
  rosters.
- **Who:** projects that render several surfaces with different flags, or whose brief leaves `retrieval_integration`
  unset.
- **Workaround:** declare `retrieval_integration` in the brief, and pass the same `--project` and `--no-scan` settings
  to every surface.
- **Tracking:** 1.0 known limitation. A notice when an inferred contract changes the roster is planned for 1.0.x
  (found by masterTeacher).

## Integrity manifest

### A re-record after deleting an enforcement module erases its pin

- **What:** `--write-integrity-manifest` records an empty digest for an enforcement module (outside the installed
  hook copies) that is missing on disk. `verify` then passes, because empty equals empty.
- **Who:** maintainers, and anyone who re-records `references/enforcement-integrity.json`.
- **Workaround:** review the manifest diff after every re-record, and reject any digest that became empty.
- **Tracking:** remediation log row 434.

### An unreadable installed hook copy is skipped, not flagged

- **What:** `verify()` treats an installed-copy path whose digest is empty as benignly absent, so a copy that is
  present but unreadable is skipped.
- **Who:** maintainers. Projects with installed gate copies.
- **Workaround:** if `--verify-integrity` passes but the gate misbehaves, check that the installed hook copies are
  readable.
- **Tracking:** remediation log row 435.

## Research tools

### The research cache still writes under `references/research-cache`

- **What:** 1.0 removes `--screenshot` from `python -m agentteams.research browser` (378), but the read-only
  retrieval grant (`Bash(python -m agentteams.research:*)`) still writes its cache under
  `references/research-cache`.
- **Who:** teams whose agents hold the retrieval grant.
- **Mitigation:** treat `references/research-cache` as agent-writable. Do not keep anything authoritative there.
- **Tracking:** CHANGELOG `[1.0.0-rc.10]`, "1.0 must-fix set", "Known residual".
