# Known Limitations in agentteams 1.0

The 1.0 release triage (@security and @adversarial, approved by the operator) classed the items below as
**documented limitations**, not release blockers. Each entry says what the limitation is, who it affects, what to
do about it, and where it is tracked. A fix may land in a 1.0.x or 1.1 release, and may change behaviour when it
does (see the [stability policy](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md)).

"Remediation log row N" refers to line N of `references/agentteams-remediation-log.csv` (the header is line 1).

---

## Project-description schema: warn only (IM item 13a)

A JSON description is checked against `schemas/project-description.schema.json` when it is loaded, and a mismatch is a `[WARN]`, not a refusal. Census on 2026-11-15: with the schema brought in line with the code, 23 of 155 descriptors in the workspace still fail it: project names with spaces (the schema documents `project_name` as slug-safe; the code tolerates spaces), a `pip_package_name` that isn't one, extra root keys, capabilities the code doesn't read, and a few type errors. `instance_maintenance`, which the code reads, is now in the schema. Refusing comes in a later release, once that census is clean.

An archetype in `selected_archetypes` with no agentteams template is reported as `[INFO]` and is not rendered; another layer (researchteam) may provide it. In a fleet run the warning is in the report's `schema_warnings`, on the first target of each workspace (it prints once per description per process).

**Tracked:** IM self-evaluation item 13 follow-up (the refusal step), logged on IM's side.

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

### `write_policy` renders only for Claude and launcher-run Codex

- **What:** `write_policy: "orchestrator-only"` needs a session sandbox that denies the runner's key directory, so
  render accepts it only for `claude` and for `codex` (Linux and macOS, launched through
  `.codex/confined-run.example.sh`). Without `write_policy_frameworks`, a render for `goose` (see the
  cooperative-grants entry above), `copilot-vscode`, `copilot-cli`, `agents-md` or `grok` is refused. With it, a
  framework not on the list renders without the switch and prints a notice. The list itself may name only
  `claude`, `goose` and `codex`, and `goose` on it is still refused. On Linux, `--check-wiring` also refuses a
  Claude team under the switch (next entry).
- **Who:** teams that render for any other framework under the switch.
- **Workaround:** set `write_policy_frameworks` to `claude` and/or `codex`; the other frameworks then render
  without the switch.
- **Tracking:** `write_policy.resolve` (`agentteams/write_policy.py`).

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
  limitations". A near-miss warning is not in 1.1; it is planned for a later release.

### A CONDITIONAL PASS needs `conditions_verified` to be exactly `verified`

- **What:** any other value, including `verified` followed by notes, does not count.
- **Who:** anyone recording CONDITIONAL PASS rows.
- **Workaround:** write exactly `verified`, and put the evidence in the `evidence` column.
- **Tracking:** the same CHANGELOG entry. A `verified:<evidence>` form is not in 1.1; it is planned for a later
  release.

### Governance reads check for symlinks by path, so a directory swapped mid-read is a race

- **What:** the decision gate refuses a symlinked `references/` directory or a symlinked switch or decisions log
  (`signing_switch.refuse_symlinked_component`): it checks each path component with `lstat`, then opens the file by
  path, `O_NOFOLLOW` on the last component only. A directory replaced by a link between that check and the open
  is not caught. The decisions log is also re-read by path for its format check and its rows.
- **Writes are not affected:** they open `references/` once with `O_DIRECTORY|O_NOFOLLOW` and work relative to that
  descriptor.
- **Who:** only a process that can rewrite the team dir while a gate runs; such a process can already edit the
  files themselves. Reading every governance file through one directory descriptor would close it; it needs the
  log's format check to read from that descriptor too, so it is not in 1.3.1.

### Through 1.3.0: a clearance recorded before a HALT's retraction took effect after it (fixed in 1.3.1, #616)

- **What (agentteams ≤ 1.3.0):** a PASS recorded while a HALT on the action was open (or, for a family HALT, before
  it) cleared the action as soon as the HALT was retracted, without anyone reviewing it at retraction time.
- **Now:** an accepted retraction of a HALT on the action, on its family or on a `derives_from` ancestor voids every
  earlier permission for that action. A PASS or CONDITIONAL PASS older than the retraction is void, and the gate says
  which rows; the newest matching row still decides, so a void row never falls through to an older one. A standing
  sandbox or S-10 exception whose decision row is older than the retraction no longer carries over, and the sandbox
  "spent off" excuse recorded before it no longer applies. `--sign-decision` refuses to sign a clearance on an id,
  or a `derives_from` parent, under an open HALT.
- **Who:** teams that retract a HALT and expected an older clearance or standing exception to resume. Record a
  fresh decision after the retraction.
- **Still open:** a waiver issued before the retraction still clears: a waiver's issue time is an unsigned
  timestamp in another log, so it cannot be ordered against the retraction row reliably.

### Through 1.3.0: a HALT on a family id did not block its instance ids (fixed in 1.3.1, #636)

- **What (agentteams ≤ 1.3.0):** a HALT on `server-apply` (or on `server-apply:remove-path:<host>`) did not stop a
  PASS on `server-apply:remove-path:<host>:<path>` from clearing it, because the HALT side matched only ids that
  start with the action, or the action's tokens inside the HALT id.
- **Now:** a HALT also blocks every id under its own id after a colon. Only `:` counts: a HALT on `branch` does not
  block `branch-delete`. Such a HALT is retracted only by a `HALT-RETRACTED` row with its own id.
- **Who:** teams whose decisions log holds an unretracted HALT on a short id that their own gates' ids extend after
  a colon (for example `deploy` and `deploy:prod`). `--verify-integrity` and `--migrate-decision-log --dry-run`
  list every unretracted HALT this reaches, consumer-defined ids included.

### Through 1.3.0: a HALT with no usable id blocked nothing (fixed in 1.3.1)

- **What (agentteams ≤ 1.3.0):** a HALT row whose `action_reviewed` was empty, blank or punctuation only matched no
  action, so a later PASS cleared the action it was meant to stop.
- **Now:** such a HALT blocks every gated action until a `HALT-RETRACTED` row whose `action_reviewed` is exactly
  its row handle, `halt-row:<N>:<digest>`, retracts it, with evidence, authenticated like any retraction.
  `--verify-integrity` and `--migrate-decision-log --dry-run` list these rows, each with its handle.
- **Limit:** a HALT whose own id is literally that handle is retracted by the same row. Do not name a HALT
  `halt-row:…`.
- **Who:** teams whose decisions log holds a HALT written without an action id. Expect gated actions to be refused
  on upgrade until each such row is reviewed and either retracted or left in force.
- **Not changed:** a free-text HALT still blocks only the actions whose id appears in it as a whole token. Write a
  HALT with the id of the action it stops.

### Before 1.3.0: a member retraction cleared every prefix-matched HALT of its action (fixed in 1.3.0, #596)

- **What (agentteams < 1.3.0):** a `HALT-RETRACTED` row such as `overwrite-001` cleared every HALT that matched
  the action `overwrite` by prefix, `overwrite-999` and a bare `overwrite` HALT included.
  - A HALT matched only by token (new in 1.0) was retracted only by its exact id.
- **1.3.0:** only a retraction of exactly the gate action (`overwrite`) clears its family's prefix HALTs, and only
  at that gate. At a member gate (for example `s10-cooldown-exempt:o/a`), a member HALT still needs a retraction
  with its own id. Any other retraction clears only the HALT with its own id. A HALT that a member retraction had cleared blocks again on
  upgrade: retract it by its own id.
- **Who (before 1.3.0):** teams with more than one open HALT for the same action.
- **Workaround (before 1.3.0):** before recording a retraction, list the open HALTs (`--verify-integrity` reports
  them), and retract by the exact HALT id.
- **Tracking:** #596; CHANGELOG 1.3.0.

### Python 3.14: an unreadable team dir, decisions log or signing switch hid HALTs (agentteams < 1.3.0)

- **What:** from Python 3.14, `Path.is_dir()`, `exists()` and `is_symlink()` return `False` on *any* `OSError`
  (3.13 raised `PermissionError`). In agentteams before 1.3.0, a team dir, decisions log or
  `agent-privilege.json` that could not be stat'd (for example after `chmod 000`, or a parent without search
  permission) therefore read as absent:
  - its HALTs were skipped in an ordinary preflight `check_clearance` and in the S-10 cooldown-exemption gate;
  - an unreadable signing switch read as "not governed".
  The check failed open, not closed.
- **Who:** every team running agentteams **< 1.3.0 on Python 3.14**.
- **Fix:** 1.3.0 uses `lstat` and treats only "not there" as absent; anything else refuses (#588, #598).
- **Workaround until you upgrade:** run agentteams on Python 3.13, or, before every preflight or signing check,
  confirm in the shell that each team dir is readable and searchable (`test -r DIR && test -x DIR`), and that its
  `references/security-decisions.log.csv` and `references/agent-privilege.json` are readable (`test -r`). Stop if
  any test fails.
- **Tracking:** #588, #598; CHANGELOG 1.3.0.

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

### The aggregate cap (constraint-relaxing-authorizations rule, clause d) is not consulted for operator-signed relaxing decisions

- **What:** `exception_registry.assert_within_aggregate_cap` runs on the adopt-merge, sandbox-relaxation and S-10
  paths, and carve-outs (`.agentteams/carveouts.json`) count against the same cap both ways: when they render, and
  (since 2026-11-12) when an exception is minted and in `--audit-exceptions`. Other Ed25519-signed relaxing
  decisions (cross-repo actions, grants) are bounded only by their own expiry.
- **Who:** operators of signing-governed workspaces who sign relaxing decisions.
- **Mitigation:** give every signed relaxing decision a short expiry, and review the decision log's open relaxing
  rows at each release or fleet round. `--audit-exceptions` covers `references/exception-registry.json` and the
  carve-outs pinned to the team, so it does not see these rows.
- **Tracking:** remediation log row 337.

### A cap planted before the exception registry was protected stays in place

- **What:** since 2026-11-12 a sandboxed team's `references/exception-registry.json` is stubbed and write-protected.
  In a team rendered before then, a confined process could have created the file with a raised
  `cap.max_active_relaxing`. The stub writer never overwrites an existing file, so the next `--update` keeps it, and
  the sandbox then protects it. A raised cap grants nothing by itself (each exception still needs an operator-signed
  decision), but it loosens the aggregate cap.
- **Who:** operators of sandboxed teams created before 2026-11-12.
- **Mitigation:** `--audit-exceptions` prints a note whenever the cap is not the default; check the file's history
  (`git log -p`) and restore the default if you didn't set it.

### Any carve-out entry takes exception-cap room, signed or not

- **What:** minting an exception and `--audit-exceptions` count every active, unexpired entry in
  `.agentteams/carveouts.json`, whether or not the registry is signed or valid, and refuse when the file can't be
  read. A draft registry therefore takes cap room before the operator signs it, and a sandboxed agent that can write
  `.agentteams/` (protected only when prompt-root protection is on) can block minting with a planted or malformed
  file. Rendering counts only valid, signed entries, so the two counts can differ.
- **Who:** operators who use carve-outs and also mint exceptions.
- **Mitigation:** the refusal names the carve-out registry; remove or fix the entry. Counting more is the
  fail-closed direction for a cap.

### The carve-out high-water mark can be rolled back with the team dir

- **What:** every sandboxed team rendered or updated since 2026-11-12 has `references/carveout-high-water.json`. In a project that commits its team
  dir, a `git checkout` or `git reset` of the team dir lowers the mark with it.
- **Who:** projects that commit `.github/agents` or another team dir.
- **Mitigation:** the registry's own history check (the last 500 commits) still refuses a re-used or lower sequence;
  the mark only adds to it. Leave the mark uncommitted (gitignore it) if your team dir is tracked.

### Generation-time grant widening does not consume `max_uses`

- **What:** a capability grant that permits `write` widens the holder's sandbox `allowWrite` when the holder team
  is generated. That path does not decrement `max_uses`: widening isn't a per-write event. An exhausted grant is
  still rejected, but while a grant is valid `expires_at` is the only bound that applies.
- **Who:** operators who issue `write` grants and expect `max_uses` to limit them.
- **Mitigation:** bound write grants by a short `expires_at`, and do not rely on `max_uses`.
- **Tracking:** `agentteams/cli/grants.py` module docstring ("On `max_uses`"). The field is reserved for a future
  per-write runtime path.

### Management directives are signed with one shared HMAC key

- **What:** a management directive is authenticated by an HMAC-SHA256 signature under the shared
  `AGENTTEAMS_MANAGEMENT_SIGNING_KEY`. Constraint-relaxing decisions require the operator's Ed25519 signature
  instead. With a symmetric key, any holder of the key can sign a directive, so the signature defeats keyless
  injection only, not a key holder.
- **Who:** workspaces that provision the management signing key.
- **Mitigation:** a directive authorizes only the exact non-destructive scope it names. Scopes that would clear
  destruction, pass a HALT, widen a capability or change governance are refused even when the signature verifies.
  Issue the key out of band to the operator and the rostered managers only, and never let it enter an agent
  session.
- **Tracking:** `agentteams/cli/management_directives.py` ("Trust model (symmetric)"), and the C-4 bounded exception
  in `agentteams/templates/universal/instruction-authority.reference.template.md`.

### A full render can't be covered by a signed `overwrite` row, because it writes CLAUDE.md

- **What:** every render writes the managed block of `.claude/CLAUDE.md`, and the effect classifier treats CLAUDE.md
  as a governance trust root. In a workspace where signing is active, a signed `overwrite` row whose effect includes
  that write is refused as non-eligible, so no honest signed row covers a full render.
- **Who:** teams with decision signing active that re-render a Claude team.
- **Workaround:** discard the render's change to `.claude/CLAUDE.md` and have the operator apply the managed-block
  diff by hand. Sign the `overwrite` row for the remaining files.
- **Note:** the gate checks the effects a row *declares*. It does not derive a render's actual writes, so a row that
  leaves CLAUDE.md out of `effect_targets` is not refused. Review render rows for that omission. Deriving the write
  set is IM item 43 (G-23-3).
- **Tracking:** 1.0 known limitation (found by mathAgents, 2026-10-16). A render mode that leaves CLAUDE.md to the
  operator and prints the diff is planned for 1.0.x.

### The other surfaces' constitution files need the operator's signature, but are not refused like CLAUDE.md

- **What:** since IM item 19, a row writing any of these needs the operator's Ed25519 signature, and a management
  directive cannot scope one:
  - `AGENTS.md` and `AGENTS.override.md`, the constitution for Codex, goose, Grok and agents-md;
  - `.goosehints`;
  - `CLAUDE.local.md`;
  - `.github/instructions/`.

  Unlike `CLAUDE.md`, they are not refused even when signed, because the frozen-surfaces feature lists `AGENTS.md`
  and `.goosehints` as owned paths, and an unsignable unfreeze would lock a frozen surface for good. The rendered
  constitution's Rule 15(c) wording ("the constitution ... even when validly signed") describes the `CLAUDE.md` case
  only.
- **Who:** signing-governed teams on those surfaces. A freeze, unfreeze or render row that lists one of these files
  now needs the operator's Ed25519 signature, where an HMAC row used to clear.
- **Tracking:** IM item 19 (G-23-2), operator ruling 2026-11-14. Rewording Rule 15(c) is IM item 44.

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

### The gate asks on some git config that runs a program, not all

- **What:** `git-alias-config` asks on `alias.`, `push.`, `remote.`, `url.`, `credential.` and `core.hooksPath`,
  `core.sshCommand`, `core.askPass`, `core.fsmonitor`, `agentteams.hookPython`, `agentteams.hookImportPath`, set through `git config`, `git -c` or `git --config-env`.
  Other git configuration also runs a program and does not ask:
  - `core.pager`, `core.editor`, `core.gitProxy`, `sequence.editor`, `diff.external`, `diff.*.textconv`,
    `merge.*.driver`, `filter.*` and `gpg.program`, by any route;
  - `include.path` through `git -c` (`git config include.path` asks, as `publication-channel`);
  - `GIT_CONFIG_COUNT` and its key/value variables ask (`publication-channel`), but the reason shown names a
    publication channel, not code execution;
  - direct edits of the config file: a Write or Edit of `.git/config`, or a shell copy, `sed -i` or redirect into it,
    asks; `git config -e` (or `git config edit`), which opens it in an editor, does not, and nor does an edit made
    by an interpreter (`python3 -c "open('.git/config', 'a')…"`).
- **Also not covered:** `git push --no-verify`, and the `GIT_SSH_COMMAND`, `GIT_SSH`, `GIT_ASKPASS`, `SSH_ASKPASS` and
  `GIT_EXEC_PATH` environment variables.
- **Who:** all Claude teams with the gate hook installed.
- **Mitigation:** the gate is a review prompt, not a boundary. Run with the Claude sandbox on, and review
  `.git/config` changes before a push.
- **Tracking:** remediation log rows 702 and 730.

### A user-level gate install treats the home directory as the project

- **What:** the gate takes its project root from where it is installed: the directory two levels above the hook
  (`<project>/.claude/hooks`, `.github/hooks` or `.grok/hooks`), plus `$CLAUDE_PROJECT_DIR`. A copy installed at
  user level, in `~/.claude/hooks`, therefore makes the home directory a project root. Every check that uses the
  roots then covers the whole home directory: the scanner's integrity manifest is looked for there, the interpreter
  pin may not point inside it, and `AGENTTEAMS_GATE_LOG` may not be written inside it.
- **Who:** anyone who installs the gate in a user-level hooks directory instead of a project.
- **Mitigation:** install the gate per project, as agentteams renders it. Put the gate log and the pinned interpreter
  outside the home directory if a user-level copy is used.
- **Tracking:** the gate does not restrict roots to the known layouts on purpose. Narrowing them would also drop
  Grok's `.grok/hooks` project from the pin and integrity checks.

### The project gate policy is advisory where nothing write-protects the gate

- **What:** the policy's digest is recorded in the installed gate. On Copilot, on Grok, and in any team whose OS
  sandbox is off, no OS write deny covers the gate or `.agentteams/gate-policy.json`. An agent that can write them
  can clear the digest, which unpins the policy. A harness timeout, or a hook removed from settings, is still the
  harness's own fail-open.
- **Who:** teams that use `.agentteams/gate-policy.json` without the Claude OS sandbox.
- **Mitigation:** run with the Claude sandbox on (the gate, its pin and the policy file get `denyWrite` and `Edit`
  rules), and check `--check-wiring`, which marks the policy ADVISORY where this applies. See
  [`gate_policy`](api-reference/gate-policy.md).
- **Tracking:** issue #456.

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
  every framework. A signed row (for example a `#210` or constraint-relaxing row) needs the current schema.
- **Who:** teams that will sign decisions.
- **Workaround:** run `--migrate-decision-log` on the team before the first signed row (see
  [Upgrading to 1.0, Step 2](migration-guide.md#step-2-upgrade-or-re-pad-a-legacy-log-migrate-decision-log)).
- **Tracking:** 1.0 known limitation. Seeding the current header is planned for 1.0.x.

### Adopted agents can't be put under orchestrator-only on Codex (fixed after 1.0)

- **What:** in 1.0, `write_policy` interop (`--interop-from … --framework codex` with the switch on) is refused, so
  adopted (bespoke) agents can't be narrowed on Codex, and their earlier projected TOMLs keep
  `sandbox_mode = "workspace-write"`. Under the launcher, the role gate still denies writes from every spawned agent,
  but the write-policy audit flags those files.
- **Who:** Codex teams whose roster includes adopted or projected agents.
- **Workaround on 1.0:** keep the Codex surface frozen (don't render or project it), or run Codex only through
  `.codex/confined-run.example.sh`.
- **Fixed:** a Codex import under the switch is accepted into a native Codex team already rendered under the switch.
  It relies on that team's launcher and role gate, renders every imported agent read-only (any `mcp_grants` for them are not honoured on Codex), and
  never replaces a native agent or `AGENTS.md`
  ([`interop_write_policy`](api-reference/interop-write-policy.md#codex-imports-since-2026-10-27); found by
  mathAgents).

### The Codex role gate is not pinned to a codex-cli version

- **What:** the generated role gate reads Codex's hook input, including fields Codex does not document. A future
  codex-cli release could change those fields, and nothing in the gate or the launcher detects that.
- **Who:** Codex teams under `write_policy: orchestrator-only`.
- **Workaround:** pin codex-cli yourself, and re-run your launcher checks after any Codex upgrade.
- **Tracking:** 1.0 known limitation. A version pin that fails closed on an untested codex-cli is planned for 1.0.x
  (found by baseAgent).

## Copilot (`--framework copilot-vscode`, `--framework copilot-cli`)

### The orchestrator and security agents exceed GitHub's custom-agent size limit

- **What:** the generated copilot-vscode `orchestrator` and `security` agent bodies can exceed GitHub's 30,000-character
  custom-agent limit (about 70,000 and 49,000 characters on one project), so Copilot truncates or rejects them.
- **Who:** copilot-vscode teams. Claude, Codex, Goose and Grok are unaffected.
- **Workaround:** rely on the Claude or Codex surface for those two roles, or trim their Project-Specific Notes.
- **Partly addressed (baseAgent P4, 2026-11-02):** a fresh Copilot render puts `## Project-Specific Notes` before the
  body's first fence, so the operator's text sits inside the limit, and `--check` fails when operator notes on disk start
  past it (move them before the first fence; `--update --merge` keeps them there). The template overrun itself remains.
- **Tracking:** 1.0 known limitation. Splitting long sections into references, with an emit-time size check, is
  planned for 1.0.x (found by masterTeacher).

### The projected model-substrate floor is not write-protected

- **What:** the Copilot surfaces emit no session sandbox, so nothing stops a Copilot session from editing or deleting
  `.agentteams/sandbox-state.json` (the `wasm_sandbox.model_substrate` floor a consumer such as baseAgent enforces).
  Claude and Goose write-protect the file, and the Codex and Grok runners protect all of `.agentteams`.
- **Who:** copilot-cli and copilot-vscode teams that set `wasm_sandbox.model_substrate`.
- **Workaround:** run `agentteams --verify-integrity` before a run that depends on the floor: it exits 1 when the file
  is absent, unusable or `off` while the last render recorded `on`. The consumer's own check is in the baseAgent
  handoff (`references/cross-orchestrator-requests/2026-W44-wasm-grant-spec-and-substrate.md` §4.3 item 8).
- **Tracking:** WASM sandbox P20 residual (Rule 12).

## WASM sandbox and the OS sandbox switch

The two switches are described in the [Sandboxing Guide, Part IX](agentteams-sandboxing-guide/reference/part-ix-switching-sandboxes-and-wasm.md).
The verdict agentteams records per framework is `wasm_enforcement` in the team manifest (printed as
`[WASM-ENFORCEMENT]`); only `enforced` and `conditional` are claims of enforcement.

### Linux: re-syncing a cooperative team next to a confined one needs a signed decision

- **What:** on Linux, a confined team emits the shared launcher `sandbox/confine-run.sh` at the repository root,
  and the sandbox-relaxation gate counts that file as OS-sandbox evidence for every framework. In a governed
  workspace (`enforce_decision_signing`), projecting a *cooperative* team in the same repository (for example
  `multi_sync` re-syncing a Copilot team beside a confined Codex team) is therefore read as turning an OS sandbox
  off, and is refused: `'sandbox-os-off:<framework>' relaxes a sandbox ... has no unconsumed decision for it`. The
  cooperative team never had a sandbox; the gate fails toward "was on" by design. macOS is not affected.
- **Who:** Linux projects that mix a confined team and a cooperative team under decision signing.
- **Workaround:** record the operator's signed PASS for `sandbox-os-off:<framework>` (`--sign-decision`, with
  `scope` equal to the action) before the re-sync. Upgrade a fresh team's decisions log first with
  `--migrate-decision-log`.
- **Tracking:** #262 (operator ruling 2026-11-08: fix the test in 1.1, narrow the evidence rule in 1.2 after a
  @security design review).

### A WASM guest cannot run Bash, git or npm

- **What:** a guest runs prebuilt `.wasm` modules, Python (CPython WASI) and JavaScript (QuickJS-ng) only, with no
  subprocess. Shell commands, git, package managers and any other native tool are not available through `run_wasm`.
- **Who:** every team with `wasm_sandbox.enabled` (and every `--framework wasm` team).
- **Workaround:** agents that need native tools keep native exec through `native_exec_keep` (default `navigator` and
  `git-operations`), a `native-exec-keep` effect that needs the operator's signature in a governed workspace.
- **Tracking:** WASM sandbox decision D3 (workloads).

### Vendor CLIs are not confined by WASM

- **What:** Claude Code, Codex, Goose, Grok and Copilot run natively. WASM confines only the code they send to
  `run_wasm`. The CLI process itself is confined only by the OS sandbox, and only once that boundary is wired.
- **Who:** every vendor-CLI team with WASM on.
- **Workaround:** keep the OS sandbox on (`os_sandbox` absent under `confined`, or `on`) and wire it.
- **Tracking:** WASM sandbox architecture A (shared brief, ARCH).

### Some providers are advisory only

- **What:** the verdict is `advisory` (instructions plus a tool offer, nothing that stops native exec) for
  copilot-vscode and copilot-cli (only an inert MCP example is emitted; no `run_wasm` in agent tools), agents-md (no
  tool channel), grok under `native_exec: replace` (only an inert MCP example is emitted; `deny` gives `declared`), goose delegates,
  goose under legacy scoping, and every top-level session on every framework. It is also `advisory` on every
  framework while the OS sandbox is off, or while the control-plane rules are not merged in the live configuration
  (a fresh Claude render reads `advisory` until the settings are merged). A narrowed sub-agent can still ask an
  agent that keeps exec (the orchestrator) to run something natively.
- **Who:** teams that read WASM on as a boundary on those hosts.
- **Workaround:** read `wasm_enforcement` / `[WASM-ENFORCEMENT]`, and run `--check-wiring` after merging settings.
  Use Claude or Goose (direct recipe runs) with the OS sandbox on where enforcement matters; on Goose that also
  needs `GOOSE_SANDBOX` on in the live Goose config, or the verdict reads `advisory` ("Seatbelt profile not wired").
- **Tracking:** WASM sandbox plan P13 (Rule 12 carve-outs 1 and 6).

### A host that runs MCP servers inside its own sandbox leaves `run_wasm` un-nested

- **What:** the `agentteams_wasm` server nests wasmtime in its own Seatbelt or bwrap profile, but Seatbelt cannot
  nest inside Seatbelt. Copilot CLI with `--sandbox` and Grok with `--sandbox workspace` start the server inside
  their own sandbox, so each call reports `os_confinement: "none"` (`unavailable:darwin`) and enforces read-only
  mounts with a per-run snapshot. The host's sandbox is then the only OS layer. Separately, `codex exec` under
  `workspace-write` blocks the call on the server's `prompt` approval, because a non-interactive run cannot answer
  it (operator spike 07, 2026-11-03, macOS).
- **Who:** Copilot CLI, Grok and Codex sessions with their own sandbox on.
- **Workaround:** read `os_confinement` in each result. Turn the host's sandbox off where you want the server's own
  nested profile. Claude Code, Goose, Copilot VS Code and these hosts with their sandbox off all report `nested`.
- **Tracking:** remediation log row `wasm-hosts` (2026-11-03).

### WASM is not supported on native Windows for now

- **What:** native Windows has no OS sandbox for the agent process (for every framework, including Claude), so WASM
  runs alone and generation prints `wasm-only-host` ("No OS sandbox on this host; WASM runs alone") and
  `wasm-windows-unsupported`. A read-only mount without an OS sandbox is enforced by a per-run snapshot copy, which
  needs `dir_fd` support that Windows lacks. So any grant with read paths refuses there, and so does the Python
  guest even with a compute-only grant: its pinned CPython runtime is always mounted read-only at `/lib`. What still
  runs is JavaScript and raw `.wasm` modules with compute-only grants. None of the Windows code paths (exit codes,
  the `SYSTEMROOT`-only environment, process-group kill) has run on a Windows host or in CI.
- **Who:** Windows hosts with `wasm_sandbox.enabled`.
- **Workaround:** run under WSL, or on a Linux or macOS host.
- **Tracking:** WASM sandbox plan P02 (open items).

### The launching-config scan is name-based

- **What:** to decide which config files must be write-protected, agentteams scans the known MCP config locations
  for launch strings that name `agentteams-wasm-mcp.py`. A shell wrapper (or any indirection) that builds the
  server path at run time does not name it, so its config is not required to be write-protected and the verdict
  can miss it.
- **Who:** operators who hand-write MCP configs that launch the server indirectly.
- **Workaround:** launch the server with the emitted command line only; do not wrap it.
- **Tracking:** WASM sandbox plan P11 (Rule 12 residual).

### The credential and config scans are point-in-time

- **What:** a granted read directory is scanned for credential-named entries and symlinks when the grant is built
  and when a run or the MCP server starts; the launching-config scan runs at render and at `--check-wiring`. A file
  created after a scan (time of check to time of use) is not seen until the next one: for example a credential
  file written into a granted read directory while an `agentteams_wasm` server is running.
- **Who:** WASM grants with `read_paths`.
- **Workaround:** grant narrow directories that nothing writes credentials into; restart the session after
  changing them.
- **Tracking:** WASM sandbox plans P01 / P10 (Rule 12 residual).

### File denies do not block macOS Keychain access

- **What:** the nested OS profiles deny reads of credential directories (`~/.ssh`, `~/.aws`, `~/Library/Keychains`
  and others), but a process can still reach Keychain items through the macOS Security framework (a Mach
  service), which file denies do not cover. A guest has no such access; the host process does.
- **Who:** macOS hosts.
- **Workaround:** keep secrets the agent must not reach out of the login Keychain, or run on a host without them.
- **Tracking:** WASM sandbox plan P02 (`wasm_exec.NEST_DENY_READS`).

### The projected model-substrate file can be deleted

- **What:** `.agentteams/sandbox-state.json` (the `wasm_sandbox.model_substrate` floor) is write-protected only
  where an OS session sandbox is emitted. With the OS sandbox off, or on Copilot (see
  [below](#the-projected-model-substrate-floor-is-not-write-protected) — its launchers do not write-protect the
  file), a session can delete or lower it. agentteams detects that only at `--verify-integrity` (exit 1 when the
  last render recorded `on`) or at the next render; a consumer that never saw `on` cannot tell a deleted file
  from "nothing projected".
- **Who:** teams that set `model_substrate: on` for a consumer such as baseAgent.
- **Workaround:** run `agentteams --verify-integrity` before a run that depends on the floor.
- **Tracking:** WASM sandbox plan P20 (Rule 12 residual).

### Convert, interop and sync apply no WASM narrowing

- **What:** `--convert-from`, `--interop-from` and multi-sync render agents from their source tools without the
  `native_exec` policy or the `run_wasm` wiring. For a WASM team they print `[WASM-ENFORCEMENT] <framework>:
  ADVISORY ONLY` and claim nothing. A re-projected team keeps its source files' (already narrowed) tools, but those
  agents get no `run_wasm` on the target.
- **Who:** teams that move a WASM team between frameworks.
- **Workaround:** re-render on the target framework from the brief (`--description … --framework <fw>`).
- **Tracking:** WASM sandbox plan P13 (open items).

### Several standing sandbox exceptions can hit the aggregate cap

- **What:** each standing relaxation (OS off, a grant widening, a native-exec keep, a runner switch) is one active
  exception in the registry, and the registry's aggregate cap defaults to one (active carve-outs pinned to the
  team count against it too). A governed workspace that needs two
  at once (for example the default `native_exec_keep` and a widened grant) is refused until the cap is raised.
- **Who:** governed workspaces with WASM on and more than one exception.
- **Workaround:** raise `max_active_relaxing` in the exception registry deliberately (it is git-tracked).
- **Tracking:** WASM sandbox METAPLAN §7 (accepted limitation).

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

### The read-only research grant still writes a cache

- **What:** 1.0 removes `--screenshot` from `python -m agentteams.research browser` (378), but the read-only
  retrieval grant (`Bash(python -m agentteams.research:*)`) still writes a cache. Since the research-hardening
  follow-up the default location is one git cannot commit: `references/research-cache` only outside a git work
  tree or when git confirms that path is ignored, otherwise a per-project subdirectory (named from a digest of
  the repository root) of the per-user cache directory (`$XDG_CACHE_HOME/agentteams/research`,
  `~/Library/Caches/agentteams/research` on macOS, else `~/.cache/agentteams/research`), with one warning. The
  per-project subdirectory keeps one project's entries from being served to another, but every project run by
  the same user can still write under that base directory. An explicit `AGENTTEAMS_RESEARCH_CACHE_DIR` is used as given;
  pointing it inside a repository is the caller's responsibility.
- **Who:** teams whose agents hold the retrieval grant.
- **Mitigation:** treat the cache directory as agent-writable. Do not keep anything authoritative there, and do not
  set `AGENTTEAMS_RESEARCH_CACHE_DIR` to a tracked path.
- **Tracking:** CHANGELOG `[1.0.0-rc.10]`, "1.0 must-fix set", "Known residual"; CHANGELOG `[Unreleased]`, research
  hardening.

### Page fetches through an environment HTTP(S) proxy return nothing

- **What:** `fetch_text` and `fetch_text_and_date` connect to the address the SSRF guard vetted, with the hostname
  kept for TLS. httpcore 1.0.x's proxy tunnel does not pass that hostname on, so through an `HTTP_PROXY` /
  `HTTPS_PROXY` / `ALL_PROXY` HTTP(S) proxy the certificate is checked against the IP and the fetch fails closed
  (empty text). One `UserWarning` per process says so; the warning is a heuristic and can be absent.
- **Who:** users whose only route to the web is an HTTP(S) proxy. SOCKS proxies are unaffected.
- **Mitigation:** use a SOCKS proxy, or exempt the destination with `NO_PROXY` (matched against the IP). The browser
  path (`browser_fetch`) is unaffected and keeps its own residual DNS-rebinding window.
- **Tracking:** CHANGELOG `[Unreleased]`, research hardening (DNS-rebinding fix).
