# `vulnscan` — AgentTeamsModule

Daily vulnerability scanning and **agent-driven remediation** of your repositories and servers,
with every action performed by an executor under grants you sign.

> *Source: `agentteams/vulnscan/` (run it as `python -m agentteams.vulnscan`)*

This page is the reference. To set it up step by step, follow the
**[automation walkthrough](../vulnscan-automation-walkthrough.md)**; for day-to-day use, see the
**[user guide](../vulnerability-remediation-guide.md)**.

**Contents:** [Architecture](#architecture) ·
[What leaves your machine](#what-leaves-your-machine) · [Files](#files-and-locations) ·
[Configuration](#configuration-vulnscanjson) · [Commands](#command-reference) ·
[Exit codes](#exit-codes) · [MCP tools](#the-agents-interface-mcp-tools) · [Grants](#grants) ·
[Escalations](#escalations) · [Ledger](#ledger-rows) · [Notifications](#notifications) ·
[Python API](#python-api) · [Known gaps](#limits-and-known-gaps)

![Three zones: your account (scan, executor, checks, ledger, you), the agent container (goose team, MCP server, request spool, egress gateway), and outside services (GitHub, servers, the private ops repo, the model API).](../book/figures/vulnscan/vulnscan-architecture.svg)

---

## Architecture

Agent mode splits remediation across three trust zones. The agents decide; only the executor acts,
and only within what you signed.

| Component | Module | Runs where | Holds | Can do |
|---|---|---|---|---|
| Deterministic scan | `engine`, `osv`, `runtimes`, `daily` | your account | nothing secret | read lockfiles, query OSV.dev and release feeds, audit servers over ssh (read-only) |
| Agent team (goose orchestrator + `vuln-remediation`) | your generated team | Docker container on an internal-only network | a dummy model key | read today's scan; write requests into the spool |
| `vulnscan` MCP server | `mcp` | inside the agent container | nothing | the agents' tools: read the scan, `request`, `escalate`, `submit_report` |
| Egress gateway | `gateway` | sidecar container | the model API key (read-only mount) | relay one route and one model; enforce the spend cap (and, for a debrief, a token cap) |
| Executor | `executor` | your account | git, `gh` and ssh credentials; the signed grants | perform a request only under a grant, after the HALT and escalation checks |
| Ledger | `ledger` | your account | the MAC key, never mounted into a container | record every decision; the review trusts nothing else |
| Operator CLI | `operator_cli`, `review`, `grant`, `publish` | your terminal | your Ed25519 signing key (read only while signing) | review, resolve, leave notes, sign grants |

**Invariants the code enforces:**

- The agent container mounts no credentials. Its only writable mounts are the request spool and its
  own goose session directory (plus tmpfs `/tmp` and home). Agent mode refuses to start until
  `agent-selftest` has passed for the current image.
- A request is executed only when a valid, unexpired, operator-signed grant covers the exact
  operation, target and remaining use count; no `@security` HALT is open on `vulnscan-remediation`
  in the decision log named **inside the grant**; and no open escalation blocks the target.
- The executor computes every change itself. `stage-fix` may only change dependency pins and
  lockfiles. `open-pr` pushes a **new** `vulnscan/*` branch to `origin` and opens the pull request
  on that same repository (`--repo HOST/OWNER/REPO` from `origin`'s URL; an `origin` not of that form
  fails before anything is pushed), never merging and never force-pushing.
  Server operations are the read-only audit and the dry run.
- A ledger row is written before (`begun`) and after (`done`, `failed`) every granted action. A
  ledger that fails verification refuses everything.
- Notifications carry fixed text only (ids, counts, known words).

### One daily run

`daily.run_daily` runs these steps:

1. **Scan** every `repo_roots` entry, this machine's runtimes and every `servers` host
   (`daily._collect`). Server audits run the **packaged** server-kit scripts with your
   `hosts.conf`, `apps.conf` and extra `ioc-*.txt` files from the server-kit directory.
2. **Summarise:** write `summary.md`, `summary.json` and the heartbeat; send `run-done` when the run
   exits non-zero or finds new findings (`daily._finish`).
3. **Only when `agent.enabled`:**
   1. **Agent run** (`agent_run.run_agent`): refuse unless a passing self-test exists for this image
      and `team_dir` and `key_file` are set; take the run lock; set aside requests left over from an
      earlier run (`executor.quarantine_pending`); fold your notes into the task (`notes-folded`
      row); start the gateway, then the agent container. Every 15 s the executor serves the agents'
      requests (`executor.serve_requests`): at most 200 per pass, escalations first, then the
      report, then granted operations. At the time cap the container is removed; the last requests
      are served; the stored report is finalized with the run state.
   2. Append the agent run's state to `summary.md`.
   3. **Publish** the report, review and summary when a publish grant is active
      (`daily.publish_run`).
   4. Send `agent-not-completed` unless the run completed.

### What leaves your machine

| Destination | What | Controls |
|---|---|---|
| OSV.dev, PyPI, nodejs.org, www.php.net, endoflife.date | package names and versions; runtime versions | HTTPS to those hosts only, no redirects |
| The npm registry (`stage-fix` in a repository with `package-lock.json`) | the lockfile's dependency tree, through `npm audit fix --package-lock-only` | npm's own client; lockfile only, no install scripts |
| The model API (through the gateway) | everything the agents read and write: the scan summary and findings, repository paths, host names, fix plans, executor results, your notes | one route, one model; **not scrubbed**. Choose an `upstream` and model provider you trust with that |
| GitHub (`open-pr`) | the fix branch and a pull request body with the fix plan and the agent's rationale (sanitised, quoted) | only to `origin` of a repository a grant covers |
| Your servers | the server-kit scripts, streamed over ssh | exact `hosts.conf` names a grant covers; audit and dry run only |
| Your private ops repo (publish grant) | the run's report, review and summary | agent text fenced; home paths, hostname and private IPs scrubbed; content scan; preflight |

**The spend cap** counts the `usage.cost` the upstream reports with each response, after the
response, so a call already in flight can overshoot it. Two backstops do not depend on reported
cost: a run token cap (`max_tokens_total`), and a refusal after `max_unmetered_calls` replies that
carry no usable usage (unless `allow_unmetered`). `spend.json` records the unmetered count. The
gateway asks the upstream to include usage on every call; OpenRouter does.

---

## Files and locations

Everything is per user. Nothing is written into a repository except the executor's `vulnscan/*`
branches and, with a publish grant, the ops repository's `reports` branch. Teams generated by
agentteams protect `~/.config/agentteams` from their agents: a sandbox write root may not reach it,
and the emitted `Edit(...)` rules deny it to the editing tools. These protections cover the default
location only; if you move the config with `AGENTTEAMS_VULNSCAN_CONFIG_DIR`, `XDG_CONFIG_HOME` or
`AGENTTEAMS_SERVER_KIT_DIR`, protect that path yourself. Treat `hosts.conf` as code: its ssh target
field is passed to `ssh`, so an option such as `-oProxyCommand=…` runs a local command.

| What | Default path | Notes |
|---|---|---|
| Config | `~/.config/agentteams/vulnscan.json` | `$XDG_CONFIG_HOME` honoured; `AGENTTEAMS_VULNSCAN_CONFIG_DIR` |
| Server kit, `hosts.conf`, `apps.conf` | `~/.config/agentteams/server-kit/` | `AGENTTEAMS_SERVER_KIT_DIR` |
| Remediation grants | `~/.config/agentteams/vulnscan-grants/vg-*.json` | mode 600 |
| Publish grants | `~/.config/agentteams/vulnscan-publish-grants/vp-*.json` | mode 600 |
| Operator notes | `~/.config/agentteams/vulnscan-notes/note-*.json` | directory must be yours and not group/world-writable |
| Operator verify keys (public) | `~/.config/agentteams/verify-keys/<key-id>.pub.pem` | must be yours and not group/world-writable |
| Operator signing key (private) | `~/.config/agentteams/keys/decision-signing-<key-id>.pem` | as written by `provision-operator-signing-key.sh`; named by `AGENTTEAMS_DECISION_ED25519_KEYFILE` |
| Ledger MAC key, high-water mark | `~/.config/agentteams/keys/vulnscan-ledger.key`, `vulnscan-ledger.hwm` | never mounted into a container |
| State | `~/.local/state/agentteams/vulnscan/` | `$XDG_STATE_HOME` honoured; `AGENTTEAMS_VULNSCAN_STATE_DIR` |
| Ledger | `<state>/ledger.jsonl` | append-only, MAC-chained |
| Heartbeat | `<state>/heartbeat.json` | read by the stale-run warning |
| Run directory | `<state>/<YYYY-MM-DD>/` | `summary.md`, `summary.json`, `fixplan-*.md`, `agent/`, `agent-report/`, `gateway/` |
| Request spool | `<state>/spool/requests/`, `results/`, `processed/` | the only path the agents write besides their sessions |
| Staged fixes | `<state>/worktrees/<repo>-<8 hex>-<timestamp>/` | executor-owned worktrees |
| Run lock | `<state>/agent.lock` | held for a whole agent run or debrief |
| Self-test record | `<state>/agent-selftest.json` | tied to the image id |

---

## Configuration: `vulnscan.json`

`init` writes the file; `daily.load_config` fills any missing key from the defaults below.

| Key | Default | Meaning |
|---|---|---|
| `repo_roots` | `[]` | Directories whose git repositories are scanned |
| `include_worktrees` | `false` | Also scan linked git worktrees |
| `servers` | `[]` | `hosts.conf` names audited each day |
| `fail_on` | `"high"` | Lowest severity that makes the run exit 1 |
| `include_dev` | `false` | Dev-only dependencies count toward `fail_on` |
| `cooldown_days` | `14` | A fix release must be at least this old (Rule S-10). The executor never goes below 14 |
| `local_runtimes` | `true` | Check Node.js, PHP and Python on this machine |
| `fix_mode` | `"plan"` | `plan` writes fix plans; `apply-worktree` stages fixes in a worktree per repo (without agents) |
| `allow_major` | `false` | Allow major-version upgrades in fix plans |
| `notify` | `"desktop"` | `none`, `desktop`, or `command:<program and args>` (the program gets the fixed message as its last argument) |
| `schedule` | `{"hour": 7, "minute": 30}` | When `schedule install` runs the job |
| `stale_after_hours` | `26` | Age after which commands warn that no daily run completed |
| `governance_dir` | `""` | Used only by `halts`, to find a decision log when no grant names one. The executor uses the directory **signed into the grant** |
| `agent` | see below | Agent mode |

The `agent` block (merged over these defaults by `agent_run.agent_cfg`):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Run the agent team after the daily scan |
| `framework` | `"goose"` | The only framework agent mode runs |
| `team_dir` | `""` | Root of the generated goose team's project (it contains `.goose/recipes/`). Must be an existing directory; not home, an ancestor of home, or at, inside or above `~/.config/agentteams` or a credential directory; and must not contain `key_file` or the operator signing key |
| `recipe` | `".goose/recipes/orchestrator.yaml"` | Recipe the container runs, relative to `team_dir` |
| `model` | `"z-ai/glm-5.3"` | The one model the gateway allows (an OpenRouter model id) |
| `upstream` | `"http://host.docker.internal:8791"` | Where the gateway sends requests. Without a local route proxy, use `"https://openrouter.ai"` |
| `key_file` | `""` | File holding `OPENROUTER_API_KEY=...` (or the bare key). Mounted read-only into the gateway only |
| `budget_usd` | `2.0` | Spend cap per run (see [What leaves your machine](#what-leaves-your-machine)) |
| `max_tokens_total` | `10000000` | Prompt + completion token cap per run, independent of reported cost (integer 1–100 000 000) |
| `max_unmetered_calls` | `20` | The gateway refuses after this many successful replies without usable (non-zero) usage or cost (integer ≥ 1). Upstream error replies (4xx/5xx) are counted separately (`upstream_errors` in `spend.json`) and do not count here |
| `allow_unmetered` | `false` | Accept replies without usage (then only the token cap, if usage has tokens, and the time cap bound a run) |
| `max_minutes` | `45` | Time cap per run; the container is removed at the cap |
| `image` | `""` | Image tag; default `agentteams-vulnscan-agent:<agentteams version>` |
| `command` | goose `run --recipe {recipe} --name {session} --with-extension "python -m agentteams.vulnscan.mcp" --params task={task}` | Command run in the container |
| `debrief_budget_usd` | `0.5` | Spend cap for `agent-chat` |
| `debrief_max_minutes` | `20` | Time cap for `agent-chat` |
| `debrief_max_tokens` | `400000` | Prompt + completion token cap for `agent-chat` |

---

## Command reference

All commands are `python -m agentteams.vulnscan <command>`. Every command except `daily` and
`schedule` first warns when the last daily run is stale.

| Command | Purpose |
|---|---|
| `scan PATH` | Scan manifests under a repo or a folder of repos |
| `inventory LOG --host H` | Analyse a server-kit audit log |
| `runtimes` | Node.js, PHP and Python on this machine |
| `fix PATH [--apply]` | Plan, or make, dependency fixes in a working tree you control |
| `init [--repo-root DIR]… [--server NAME]…` | Write a starter config (never overwrites) |
| `daily` | One full run (scan; with agent mode, the agent run and publishing) |
| `schedule install\|remove\|status [--apply] [--hour H] [--minute M]` | Manage the daily job (launchd on macOS, else a systemd user timer, else cron). Dry run without `--apply` |
| `server-kit [DEST]` | Export the server scripts (keeps an existing `hosts.conf` / `apps.conf`) |
| `grant sign\|list\|revoke` | Standing remediation grants ([Grants](#grants)) |
| `act OP TARGET [--rationale R]` | Run one grant-covered action yourself, through the executor |
| `serve-requests` | Serve the spooled requests once. Refuses while a run holds the run lock; has no run, so it refuses `report` requests |
| `review [--since DATE] [--gh-replies]` / `review --ack [--note N]` | After-action review; `--ack` records that you reviewed it |
| `agent-image build --goose-version V --goose-sha256 S [--python-image IMG] [--source DIR]` | Build the agent image from an agentteams source checkout |
| `agent-selftest` | Prove the container's isolation for the current image ([checks](#selftestcfg)) |
| `agent-run` | One agent run on the latest scan |
| `halts` | Open escalations and decision-log HALTs, with how to clear each |
| `resolve ID --evidence TEXT [--key-id ID]` | Resolve an escalation ([Escalations](#escalations)) |
| `say TEXT [--target T] [--days N]` · `say --from-gh REF [--repo O/R]` · `say --list` · `say --remove ID` | Operator notes for the next run |
| `publish-grant sign\|list\|revoke` | The grant that lets the executor push reports |
| `publish [--run DATE]` | Publish a run's files now |
| `agent-chat [--run DATE]` | Resume a past run's goose session interactively |

The signing commands (`grant sign`, `publish-grant sign`, `resolve` for serious kinds) also accept
`--allow-checkout-signing` and `--confirm-review-sha256` ([Signing](#signing)).

### Exit codes

| Command | 0 | 1 | 2 | 3 | Other |
|---|---|---|---|---|---|
| `scan`, `inventory`, `runtimes`, `daily` | clean | findings at or above `--fail-on` (`daily`: the config's `fail_on`) | usage or setup error (`daily`: no config) | partially unscanned | 4: malicious package or compromise indicator |
| `fix` | nothing held back | something held back or refused | usage error | a feed failed (partially unscanned) | 4: a malicious pin |
| `schedule` | done or dry run | errors | — | — | — |
| `grant sign` | signed and installed | not signed (declined, preflight) | refused (bounds; does not verify) | — | — |
| `grant revoke`, `publish-grant revoke` | revoked | no such grant | — | — | — |
| `act` | done | refused | no config | failed | — |
| `serve-requests` | served | — | run lock held, or no config | — | — |
| `agent-image build` | built | build failed | no config | — | — |
| `agent-selftest` | passed | failed | no config | — | — |
| `agent-run` | completed | — | no scan, or no config | not run, error or timed out | — |
| `halts` | nothing blocks | something blocks | — | — | — |
| `resolve` | resolved | not resolved (declined, not signed) | refused | — | — |
| `say` | done | no such note | refused | — | — |
| `publish-grant sign` | signed and installed | not signed | refused | — | — |
| `publish` | published | no publish grant | no such run, or no config | failed or refused | — |
| `agent-chat` | the session's exit code | | refused | | 75: run lock held · 124: time cap |

`init`, `server-kit`, `review` and every `list` exit 0.

---

## The agents' interface: MCP tools

The `vulnscan` MCP server (`python -m agentteams.vulnscan.mcp`, stdio) runs inside the agent
container. Everything it returns that came from advisories or repositories is prefixed as **data**
(C-4).

| Tool | Arguments | Effect |
|---|---|---|
| `get_summary` | — | Today's `summary.md` |
| `list_findings` | `min_severity` (`critical`…`any`), `target` | Findings as JSON (at most 500) |
| `get_fix_plans` | — | The deterministic fix plans |
| `request` | `op` (`stage-fix`, `open-pr`, `server-audit`, `server-dryrun`), `target`, `rationale` | Queues a request; runs only if a grant covers it |
| `escalate` | `target`, `kind` (`malicious`, `ioc`, `injection`, `integrity`, `other`), `evidence` | Queues an escalation; needs no grant |
| `submit_report` | `markdown` (≤ 200 000 UTF-8 bytes) | Queues the run report; needs no grant |
| `request_status` | `id` | The executor's result, or `pending` |
| `list_requests` | — | The 50 most recent results |

**Spool request file** (`<state>/spool/requests/<id>.json`, what the MCP server writes and the
executor validates):

| Field | Rule |
|---|---|
| `id` | 32 lowercase hex characters; equals the file name; never reused (replay is refused) |
| `op` | an `OPS` value, or `report` / `escalate` |
| `target` | absolute repo path (`stage-fix`, `open-pr`), exact `hosts.conf` name (server ops), any target (`escalate`), `"run"` (`report`) |
| `rationale` | string, kept in the ledger |
| `created` | ISO timestamp |
| `payload` | only for `report` (`{"markdown"}`) and `escalate` (`{"kind", "evidence"}`) |

No other key is accepted. A request is at most 64 KiB (256 KiB for `report`). Symlinks and
non-regular files are refused and moved to `processed/rejected-*`.

---

## Grants

Two kinds of operator-signed grant exist, each with its own Ed25519 purpose tag, so a signature
for one can never verify as the other.

| | Remediation grant (`grant`) | Publish grant (`publish-grant`) |
|---|---|---|
| Purpose tag | `agentteams-vulnscan-remediation-grant-v1` | `agentteams-vulnscan-publish-grant-v1` |
| Covers | `ops` on repositories under `repo_roots` and exact `hosts` | pushing one run's report, review and summary to one repository |
| Signed fields | `grant_id`, `issued`, `expires`, `ops`, `repo_roots`, `hosts`, `max_uses`, `key_id`, `governance_dir` | `grant_id`, `issued`, `expires`, `ops_repo`, `remote_url`, `node_id`, `owner_id`, `governance_dir`, `max_uses`, `key_id` |
| Lifetime | at most 30 days | at most 30 days |
| Uses | at most 500 | at most 400 |
| Valid at once | at most 2 (more: all refused) | at most 1 (more: all refused) |
| Gate action (HALT) | `vulnscan-remediation` | `vulnscan-publish` |

**Operations.** v1 grants may name `stage-fix`, `open-pr`, `server-audit` and `server-dryrun`.
`merge` and `server-apply` are reserved: the CLI lists them, and `grant.build` refuses them.

**Bounds `grant.build` enforces:**

- at least one operation, and only v1 operations;
- `days` 1–30 and `max_uses` 1–500;
- `repo_roots` are resolved to absolute paths and may not be `/` or your home directory itself;
  `stage-fix` and `open-pr` need at least one;
- `hosts` must be plain names, never `all`; server operations need at least one. Whether a name is
  in `hosts.conf` is checked when the operation runs;
- `governance_dir` must contain `references/security-decisions.log.csv`.

**Scope.** A repository is covered when its path is inside one of the `repo_roots`, so a root covers
every repository below it. Name roots explicitly. Whatever a grant says, the executor refuses
repo operations in the same repository (compared by git common directory, so worktrees count) as:
the agentteams installation it runs from, `agent.team_dir`, the `governance_dir` of every grant file
(remediation and publish, valid or not), and any publish grant's ops repository. Third-party clones
are not excluded; leave them out of your roots. If agentteams runs from a virtual environment inside a
repository, that repository is refused too. A repository check that does not answer within 30 s
refuses the request.

### Signing

Every signature goes through one flow (`grant.operator_sign`):

1. **Preflight, key unread.** `AGENTTEAMS_DECISION_ED25519_KEYFILE` must name your private key
   file; the signing code's integrity is checked; the install-location check refuses an
   `agentteams` an agent could edit (inside a git work tree, on an agent-writable path).
   `--allow-checkout-signing` overrides that check, which means signing with code an agent may have
   edited.
2. **Display.** The banner and every signed field, then the review digest.
3. **Confirm** with `y` at the `Sign this payload? [y/N]` prompt, or, without a terminal, by re-running
   with `--confirm-review-sha256` set to the digest the first run printed.
4. **Sign** with a single read of the key.

`grant sign` and `publish-grant sign` then verify the record against
`~/.config/agentteams/verify-keys/<key-id>.pub.pem` and install it. They do **not** install the
public key; put it there yourself (see the
[walkthrough](../vulnscan-automation-walkthrough.md#step-5-provision-your-operator-key)).

**What the digest covers.** `operator_signing.review_choices`: the record without the fields each
run mints (`grant_id`, `issued`, `expires`), plus the exact lifetime (`expires - issued`), with
the vulnscan config directory as the digest base. So a digest printed by a run without a terminal
confirms the next run (`--confirm-review-sha256`), from any working directory, and any change to
what you chose is refused. The display marks the minted fields. A digest is a standing approval of
those choices, not a one-time token: the same digest confirms them again later. Core
`--sign-mcp-direct-grant` digests the same way, with the target project as its base. (Before 2026-10-22 the digest covered the minted fields and the working directory,
so the flag could never match.)

---

## Escalations

An agent calls `escalate` when it hits a stop condition. The executor records it in the ledger
and, until you resolve it, refuses every grant use in its scope.

| Kind | Raised for | Scope the executor sets | Resolve with |
|---|---|---|---|
| `malicious` | an OSV `MAL-*` package | every target (`*`) | your Ed25519 signature |
| `ioc` | a compromise indicator on a host | every target | your Ed25519 signature |
| `integrity` | a ledger or report integrity failure | every target | your Ed25519 signature |
| `injection` | data that tries to direct the agent | the canonical target | your Ed25519 signature |
| `other` | anything you must decide first | the canonical target | `y` at a terminal (MAC'd ledger row) |
| `escalation-flood` | (executor) more than 10 escalations in one run, or more than 200 spool requests at once | every target | your Ed25519 signature |

The canonical target is the **main** repository's top-level path (a subdirectory or a linked
worktree maps to it) or an exact `hosts.conf` name. A target that does not canonicalise blocks
everything. A repeat of an open escalation (same kind and scope) records nothing new.

**Escalations and HALTs are separate.** An escalation is the executor's block, raised by an agent and
cleared with `resolve`. A HALT is a `@security` verdict in the team's decision log; Rule S-VM tells
`@security` to HALT on a malicious package or a compromise indicator, so both may be open for the
same incident. Resolving an escalation never retracts a HALT. A HALT is cleared only by an
Ed25519-signed `HALT-RETRACTED` decision row (`agentteams --sign-decision`); `halts` prints the
spec to sign.

---

## Ledger rows

`<state>/ledger.jsonl`, one JSON object per line. Every row has `seq`, `ts`, `kind`, `prev` (the
previous row's MAC) and `mac` (HMAC-SHA256 under the ledger key).

| `kind` | Written when | Fields |
|---|---|---|
| `begun` | a grant use is reserved, before acting | `grant_id`, `op`, `target`, `request_id` |
| `done` (granted op) | an action completes | `grant_id`, `op`, `target`, `request_id`, `detail`, `rationale` |
| `done` (`report`, `escalate`) | an internal op is recorded | `op`, `target`, `request_id`, `detail` |
| `failed` | an action fails | `grant_id`, `op`, `target`, `request_id`, `detail` |
| `refused` | a request is refused (no grant, HALT, escalation, malformed, replayed, stale, overflow) | `op`, `target`, `request_id`, `detail` |
| `refused` (`op: publish`) | a publish is refused before a use is reserved | `op`, `grant_id`, `target`, `run_id`, `detail` |
| `escalation` | an escalation opens | `esc_id`, `esc_kind`, `scope`, `evidence`, `request_id`, `run_id` |
| `resolved` | an escalation is resolved | `esc_id`, `esc_kind`, `scope`, `evidence`, `method` (`operator-mac` or `operator-ed25519`); signed ones add `issued`, `key_id`, `signature` |
| `notes-folded` | notes go into a run's task | `run_id`, `notes` (ids and sha256s only), `problems` |
| `publish-begun` | a publish use is reserved | `grant_id`, `op`, `target`, `run_id`, `path`, `files` |
| `publish-done` | a publish is pushed | `grant_id`, `op`, `target`, `run_id`, `commit`, `path`, `fenced_injection_hits` |
| `publish-failed` | a publish fails after its use was reserved | `grant_id`, `op`, `target`, `run_id`, `local_commit`, `detail` |
| `ack` | you acknowledge a review | `detail` |

---

## Notifications

`notify.send` renders one of these fixed messages. A field must be an id, a count or a known state
or kind word; otherwise nothing is sent.

| Key | Message |
|---|---|
| `escalation` | Escalation {id} ({kind}) opened — remediation is blocked for its scope. Run: vulnscan halts |
| `halt-refusal` | A @security HALT blocked remediation this run. Run: vulnscan halts |
| `run-done` | Daily run {state}: {findings} findings ({new} new). Run: vulnscan review |
| `agent-not-completed` | Agent run {state}. Run: vulnscan review |
| `publish-failed` | Publishing the run report FAILED (kept locally). Run: vulnscan review |
| `publish-done` | Run report published to the ops repository. |

---

## Python API

The CLI is the supported interface. These functions are what it calls; use them to script around
vulnscan or to test an integration.

### `agentteams.vulnscan.executor`

#### `act(op, target, cfg, *, rationale, request_id, payload, run_dir, run_id)`

Authorize and perform one action, ledgering it: the single entry point for every operation.
Internal operations (`report`, `escalate`) need no grant. Returns
`{"status": "done" | "failed" | "refused", ...}`; a refusal is a result, not an exception. A ledger
that fails verification refuses everything without appending; a request id already in the ledger is
refused as a replay.

#### `serve_requests(cfg, *, run_dir, run_id, deadline)`

Process the pending spool requests once: escalations first, then reports, then granted operations.
Reads at most `MAX_REQUESTS_PER_PASS` (200); more are moved aside unread and open an
`escalation-flood`. Starts no new request after `deadline` (a `time.monotonic()` value). Each result
is also written to `<spool>/results/<id>.json`. Without `run_dir`, `report` requests are refused.

#### `authorize(op, target, request_id, cfg)`

Call inside `ledger.locked()`. Refuses a target an open escalation blocks, a repo operation on a
publish grant's ops repository, a target no grant covers, exhausted uses, a missing decision log or
an open HALT; otherwise appends the `begun` row and returns the grant. Raises `ExecError`.

#### `canonical_target(target)`

The main repository's top-level path for a path inside a git repository (linked worktrees map to
their repository), an exact `hosts.conf` name unchanged, or `None`.

#### `quarantine_pending(reason)`

Move every pending request to `processed/stale-*` unprocessed, with one `refused` ledger row.
Returns how many were moved. Called at the start of each agent run.

#### `new_run_id(run_dir)`

`"<run date>-<6 hex>"`.

### `agentteams.vulnscan.grant`

#### `grant.build(*, ops, repo_roots, hosts, days, max_uses, key_id, governance_dir, now)`

An unsigned grant, after every bound above. Raises `GrantError`.

#### `grant.sign(record, *, confirm_sha256, allow_checkout)`

Sign through [the shared flow](#signing). Returns the signed record, or `None`.

#### `grant.verify(record, *, now)`

Shape, bounds, expiry and signature. Raises `GrantError`.

#### `grant.install(record)`

Write a signed grant (mode 600). Returns its path.

#### `grant.revoke(grant_id)`

Delete a grant (narrowing needs no signature). Returns whether one was removed.

#### `grant.load_active(*, now)`

`(valid grants, problems)`. More than two valid grants refuses all of them.

#### `covers(record, op, target)`

Whether the grant's scope includes `op` on `target` (use counts are the ledger's).

#### `verify_key(key_id)`

The public key PEM from `~/.config/agentteams/verify-keys/<key-id>.pub.pem`, refused when the file
is not yours or is group- or world-writable. Raises `GrantError`.

#### `operator_sign(record, *, digest_kind, banner, fields, values, confirm_sha256, allow_checkout)`

The shared signing flow described under [Signing](#signing). Returns the signed record, or `None`
after printing why nothing was signed.

### `agentteams.vulnscan.publish`

#### `publish.build(*, ops_repo, remote_url, node_id, owner_id, governance_dir, days, max_uses, key_id, now)`

An unsigned publish grant, after every bound. Raises `PublishError` (a `GrantError`).

#### `publish.sign(record, *, confirm_sha256, allow_checkout)`

Sign through [the shared flow](#signing). Returns the signed record, or `None`.

#### `publish.verify(record, *, now)`

Shape, bounds, expiry and signature. Raises `PublishError`.

#### `publish.install(record)`

Write a signed publish grant (mode 600). Returns its path.

#### `publish.revoke(grant_id)`

Delete a publish grant. Returns whether one was removed.

#### `publish.load_active(*, now)`

`(valid publish grants, problems)`. More than one valid grant refuses all of them.

#### `preflight(grant_record)`

Problems that stop a publish (empty means it may proceed): the checkout's `remote.origin.url` is
the pinned one with no `pushurl` or `insteadOf` redirection; the repository has the pinned node id
and is private; its owner and the authenticated `gh` user have the pinned owner id; Actions and
Pages are off; the `reports` branch has no `.github/workflows/`; no collaborator other than the owner
(`affiliation=all`), no pending invitation, and no deploy key (read-only keys also expose reports).
Any API error refuses. GitHub Apps and tokens with access are not visible to it.

#### `publish(grant_record, run_dir, run_id, files)`

Check the files first (scrub, caps of 10 files and 1 MB, content scan), refusing **before** a use is
reserved; then ledger `publish-begun`, preflight, commit `reports/<date>/<run_id>/<name>` in an
executor-owned worktree, preflight again, push without force. Returns `status` `done`, `failed`
(the local commit is kept under `refs/vulnscan-publish/<run_id>`) or `refused`.

#### `rewrite_paths(text)`

Scrub local identity: home paths become `~`, this machine's hostname `<this-host>`, RFC 1918
addresses `<private-ip>`.

#### `fenced_lines(text)`

Line numbers inside closed ```` ```text ```` fences (what `report_store.fence` writes), by CommonMark's
rules: an opener of 3 or more backticks plus `text`, up to 3 spaces of indent; closed by a run of at
least as many backticks with only trailing whitespace; an unclosed fence fences nothing; other
fences (`~~~`, other info strings) are tracked but never exempt. A file with an HTML-block start
(`<` after up to 3 spaces) outside a fence, or with a line break CommonMark does not recognise
(`\v`, `\f`, `\x1c`–`\x1e`, NEL, U+2028, U+2029), gets no exemption at all. The content scan allows
injection-pattern hits inside these fences (an escalation's evidence quotes what it reports) and
counts them; secrets and personal data block anywhere. Container blocks (lists, block quotes) are
not modelled; they only make the check stricter than GitHub's rendering.

#### `all_ops_repos()`

The ops repository of every publish-grant file, valid or not. Used only to refuse remediation there.

### `agentteams.vulnscan.escalations`

#### `record(kind, target, evidence, *, request_id, run_id, canonical)`

Record an agent escalation; call inside `ledger.locked()`. Returns the new or the already-open
`Escalation`. Raises `EscalationError`.

#### `record_flood(detail, *, run_id, request_id)`

Open (or return the open) `escalation-flood`. Returns `(escalation, newly_opened)`.

#### `open_escalations(rows)`

Escalations with no later `resolved` row, in ledger order.

#### `blocking(target, rows)`

The open escalation that blocks a canonical target (a `*` one first), or `None`.

#### `resolve_other(esc_id, evidence)`

Resolve an `other` escalation with your reason (a MAC'd `resolved` row). Refuses serious kinds.

#### `build_resolution(esc_id, evidence, key_id, *, now)`

The unsigned resolution record for a serious escalation.

#### `sign_resolution(record, *, confirm_sha256, allow_checkout)`

Sign it through the shared flow. Returns the signed record or `None`.

#### `apply_signed_resolution(record)`

Verify the signature, then write the `resolved` row (`method: operator-ed25519`).

### `agentteams.vulnscan.notes`

#### `add(text, *, target, days, source, _extra)`

Store one note (at most 2048 bytes; at most 365 days, or no expiry). `_extra` is internal.

#### `active(now)`

`(notes, problems)`: the unexpired notes, oldest first. Never raises.

#### `fold(task, notes)`

Append the notes to a task as a delimited, template-escaped block (at most 8192 bytes; the oldest
are dropped first). Returns `(task, [{"id", "sha256"}])`.

#### `gh_replies(prs, *, exclude_comment_ids)`

List the comments on pull requests, as data. Nothing is stored.

#### `from_gh(comment_id, *, repo, target, days, confirm)`

Promote one `/vulnscan note` comment to a note. The author must be the authenticated `gh` user, and
`confirm` sees the fetched text before anything is stored.

### `agentteams.vulnscan.agent_run`

#### `run_agent(cfg, run_dir, *, task, run_id)`

One agent run. Returns the status (also written to `<run>/agent/status.json`) with `run_state` in
`completed`, `agent-error`, `timed-out` or `not-run`, and `run_id`. Launcher failures end as
`agent-error` rather than raising.

#### `fold_notes(task, run_id)`

Fold the active notes into the task and ledger which ones went in.

#### `selftest(cfg)`

Run the isolation self-test container (with the gateway it needs) and record the result against
the image id and `flags_sha256` (a hash of the container and gateway flags and the self-test
itself). Refuses without `agent.key_file` or with an invalid `team_dir`. Every check must be
`true`:

| Check | Meaning |
|---|---|
| `no_credential_paths` | no `~/.ssh`, `~/.config/gh`, `.git-credentials`, `.netrc`, `.docker`, `.npmrc`, `.pypirc`, `.aws`, `.gnupg`, `.config/agentteams/keys` |
| `no_token_env` | no `GH_TOKEN`, `GITHUB_TOKEN`, `SSH_AUTH_SOCK`, `AWS_ACCESS_KEY_ID`; the model key is the dummy |
| `internet_blocked` | neither example.com nor OpenRouter is reachable directly |
| `gateway_reachable` | the gateway answers on the internal network |
| `readonly_rootfs`, `readonly_run_dir`, `readonly_results` | the root filesystem, run directory and results are read-only |
| `spool_writable` | the agent can queue requests |
| `no_capabilities`, `no_new_privs`, `non_root` | no Linux capabilities, no privilege gain, a non-root uid (the image runs as 10001) |

#### `selftest_ok(cfg)`

`(ok, reason)`: whether a passing self-test exists for the current image and the current container
flags (`flags_sha256`).

### `agentteams.vulnscan.debrief`

#### `run_debrief(cfg, run_dir)`

Resume a past run's goose session with the terminal attached, a read-only spool, and its own
gateway with the debrief caps.

#### `debrief_max_tokens(a)`

The debrief's token cap from the agent settings `a` (as returned by `agent_run.agent_cfg`):
`debrief_max_tokens`, default 400 000.

### `agentteams.vulnscan.daily`

#### `run_daily(cfg, *, today)`

One daily run. Returns `(report, exit code, run directory)`.

#### `publish_run(cfg, run_dir, run_id)`

Publish a run when a publish grant is active; `None` otherwise.

#### `load_config(path)`

The config with defaults filled in. Raises `FileNotFoundError` when there is none.

### `agentteams.vulnscan.review`

#### `review.build(*, since, for_publish, gh_replies)`

Render the after-action review. `for_publish=True` puts every agent- or repository-derived text
inside a fence and scrubs local identity; `gh_replies=True` lists replies on the agents' pull
requests (it calls `gh`).

#### `acknowledge(note)`

Write the `ack` row that closes the current review window.

#### `overdue(days)`

Whether actions have gone unreviewed for more than `days` (default 3).

### `agentteams.vulnscan.report_store`

#### `save(run_dir, markdown, *, request_id)`

Store the agent's report fenced and inert under `<run>/agent-report/report.md`.

#### `finalize(run_dir, run_state)`

Record the run's final state in the stored report's header.

#### `render_for_review(run_dir)`

The review section for the stored report.

### `agentteams.vulnscan.notify`

#### `send(cfg, key, **fields)`

Deliver one fixed notification. Returns whether a notifier ran; an unsafe field is refused and
reported on stderr, never raised.

### `agentteams.vulnscan.paths`

#### `run_dirs(state)`

The completed run directories (those with a `summary.json`), oldest first.

---

## Limits and known gaps

- **Start supervised.** Run `agent-run` once with a narrow grant and read the review before setting
  `agent.enabled` (walkthrough
  [Steps 8–10](../vulnscan-automation-walkthrough.md#step-8-sign-a-narrow-first-grant)).
- **macOS Seatbelt is not the agent sandbox.** Agent mode uses the Linux container, which is what
  `agent-selftest` verifies.
- **The model provider sees the agents' context** ([What leaves your machine](#what-leaves-your-machine)).
  vulnscan does not rate models.
- **goose session facts** used by `agent-chat` are verified for goose 1.10 and later
  (`debrief.verify_goose_sessions` lists its sources and anything unverified).
