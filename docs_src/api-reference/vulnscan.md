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
[Exit codes](#exit-codes) · [JSON output](#json-output) · [MCP tools](#the-agents-interface-mcp-tools) · [Grants](#grants) ·
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
| Egress gateway | `gateway` | sidecar container | the model API key (read-only mount) | relay one route and one model; enforce the spend cap and a token cap on every run and debrief (`--max-tokens-total`, from `agent.max_tokens_total` or `agent.debrief_max_tokens`) |
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
- The executor computes every change itself. `stage-fix` may only raise dependency pins and change
  lockfiles within the rules of [`validate_diff`](#validate_diffwt-base). Before any fetch it
  refuses a repository that git refuses to open (ledgered `refused`), or whose own git config
  defines a filter driver (ledgered `failed`) (`fix.git_config_problem`). `open-pr` pushes a **new** `vulnscan/*` branch to `origin` and opens the pull request
  on that same repository (`--repo HOST/OWNER/REPO` from `origin`'s URL; an `origin` not of that form
  fails before anything is pushed), never merging and never force-pushing. It is idempotent
  ([`open_pr`](#open_prrepo-rationale)).
  Server operations are the read-only audit and the dry run.
- A ledger row is written before (`begun`) and after (`done`, `failed`) every granted action. A
  ledger that fails verification refuses everything.
- Notifications carry fixed text only (ids, counts, known words).

### One daily run

`daily.run_daily` takes the daily run lock (`<state>/daily.lock`) first. While another daily run
holds it, the second run writes nothing, prints why on stderr and exits 3. Then it runs these steps:

1. **Scan** every `repo_roots` entry, this machine's runtimes and every `servers` host
   (`daily._collect`). Server audits run the **packaged** server-kit scripts with your
   `hosts.conf`, `apps.conf` and extra `ioc-*.txt` files from the server-kit directory.
2. **Summarise:** write `summary.md`, `summary.json` and the heartbeat (atomically); send `run-done`
   when the run exits non-zero or finds new findings (`daily._finish`). NEW marks compare with the
   newest readable earlier `summary.json`; an unreadable one is skipped with a note in `summary.md`.
   A write that fails (disk full) makes the run exit 3 (4 on compromise indicators), and `run-done`
   is still sent.
3. **Only when `agent.enabled`**, and only when step 2 wrote the summary and the heartbeat (a failed
   write skips the agent phase):
   1. **Agent run** (`agent_run.run_agent`): take the run lock (when another run or a debrief holds
      it: `not-run`, and nothing in the run directory is written); then refuse (`not-run`) on the
      first failing check (after moving an earlier run's record aside, see `run_records`), in this order: `team_dir` and `key_file` are set, docker is installed, a
      passing self-test exists for this image and these container flags, `team_dir` passes its
      checks, the run limits are valid. Then set aside requests left over from an
      earlier run (`executor.quarantine_pending`); fold your notes into the task (`notes-folded`
      row); start the gateway, then the agent container. Every 15 s the executor serves the agents'
      requests (`executor.serve_requests`): at most 200 per pass, escalations first, then the
      report, then granted operations. At the time cap the container is removed; the last requests
      are served; the run's own report is finalized with the run state. A run that would end
      `completed` without a report of its own ends `agent-error` (the reason quotes the
      transcript's last error line). A SIGTERM (launchd unload,
      logout) removes both containers, records `agent-error` and exits 143.
   2. Append the agent run's state to `summary.md`.
   3. **Publish** the report, review and summary when a publish grant is active
      (`daily.publish_run`).
   4. Send `agent-not-completed` unless the run completed.

### What leaves your machine

| Destination | What | Controls |
|---|---|---|
| OSV.dev, PyPI, nodejs.org, www.php.net, endoflife.date | package names and versions; runtime versions | HTTPS to those hosts only, no redirects |
| The npm registry (`stage-fix` in a repository with `package-lock.json`) | the lockfile's dependency tree, through `npm audit fix --package-lock-only` | npm runs on a scratch copy holding only `package.json` and the lockfile, with empty user and global npm config, the public registry, `--strict-ssl=true` and `--ignore-scripts`; the repository's `.npmrc` is never read. npm workspaces fail closed (npm errors, nothing is staged) |
| The model API (through the gateway) | everything the agents read and write: the scan summary and findings, repository paths, host names, fix plans, executor results, your notes | one route, one model; **not scrubbed**. Choose an `upstream` and model provider you trust with that |
| GitHub (`open-pr`) | the fix branch and a pull request body with the fix plan and the agent's rationale (sanitised, quoted) | only to `origin` of a repository a grant covers |
| Your servers | the server-kit scripts, streamed over ssh (`remote.sh` streams only `L1-audit.sh` to `L5-containers.sh`) | exact `hosts.conf` names a grant covers; audit and dry run only. The audit (which the agent then reads) never reads process environments, unit files, journals or `.env` contents and shows each service by its executable path only, so service arguments and inline secrets never reach its output. Only a hand-run `L1-audit.sh --deep` reads process environments, and nothing in vulnscan passes it |
| Your private ops repo (publish grant) | the run's report, review and summary | agent text fenced; home paths, hostname and private IPs scrubbed; content scan; preflight |

**The spend cap** counts the `usage.cost` the upstream reports with each response, after the
response. Upstream calls are serialised, so at most one call is in flight: the one that crosses a
cap can overshoot it. Two backstops do not depend on reported
cost: a run token cap (`max_tokens_total`), and a refusal after `max_unmetered_calls` replies that
carry no usable usage (unless `allow_unmetered`). `spend.json` records the unmetered count and is
written atomically (a temporary file, then a rename). The gateway asks the upstream to include
usage on every call; OpenRouter does. The gateway refuses to start (exit 2) unless `--budget-usd`
is finite and ≥ 0, `--max-tokens-total` is ≥ 0 (0: no token cap), `--listen` is `HOST:PORT` and
`--upstream` is an http(s) URL; a non-numeric `Content-Length` gets HTTP 400.

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
| Remediation grants | `~/.config/agentteams/vulnscan-grants/vg-*.json` | mode 600; each file must be a regular file you own, not group/world-writable, not a symlink. The directory must be a real directory (not a symlink) you own, not group/world-writable, or every grant is refused |
| Publish grants | `~/.config/agentteams/vulnscan-publish-grants/vp-*.json` | as for remediation grants |
| Operator notes | `~/.config/agentteams/vulnscan-notes/note-*.json` | directory must be yours and not group/world-writable |
| Operator verify keys (public) | `~/.config/agentteams/verify-keys/<key-id>.pub.pem` | the directory and the file must be yours, not group/world-writable and not symlinks |
| Operator signing key (private) | `~/.config/agentteams/keys/decision-signing-<key-id>.pem` | as written by `provision-operator-signing-key.sh`; named by `AGENTTEAMS_DECISION_ED25519_KEYFILE` |
| Ledger MAC key, high-water mark | `~/.config/agentteams/keys/vulnscan-ledger.key`, `vulnscan-ledger.hwm` | never mounted into a container |
| State | `~/.local/state/agentteams/vulnscan/` | `$XDG_STATE_HOME` honoured; `AGENTTEAMS_VULNSCAN_STATE_DIR` |
| Ledger | `<state>/ledger.jsonl` | append-only, MAC-chained |
| Heartbeat | `<state>/heartbeat.json` | read by the stale-run warning; written atomically |
| Daily run lock | `<state>/daily.lock` | held for a whole daily run; a second run exits 3 |
| Run directory | `<state>/<YYYY-MM-DD>/` | `summary.md`, `summary.json`, `fixplan-*.md`, `agent/` (earlier agent runs of the day under `agent/runs/<run id>/`), `agent-report/`, `gateway/` |
| Request spool | `<state>/spool/requests/`, `results/`, `processed/` | the only path the agents write besides their sessions |
| Staged fixes | `<state>/worktrees/<repo>-<8 hex>-<YYYYmmddHHMMSS>-<6 hex>/` (repository name, a hash of its path, the UTC time, a random suffix) | executor-owned worktrees; removed once `open-pr` has a pull request (the branch is kept) |
| Run lock | `<state>/agent.lock` | held for a whole agent run or debrief |
| Self-test record | `<state>/agent-selftest.json` | tied to the image id |

---

## Configuration: `vulnscan.json`

`init` writes the file; `daily.load_config` fills any missing key from the defaults below, then
checks each key's type and range. A file that is not a JSON object, or a key with a wrong value, is
refused with a message naming the file and the key (`config ... is unusable: ...: fail_on must be
one of ...`), and every command that reads the config exits 2.

Every `agent.*` key is checked at load too (`agent_run.validate_agent`), not only `agent.enabled`
(a bool):

- `team_dir`, `key_file`, `image`: strings, empty allowed (paths are checked against the filesystem
  only before a mount, by `team_dir_problem`);
- `model`, `framework`, `recipe`: non-empty strings;
- `upstream`: an `http://` or `https://` URL with a host;
- `command`: a non-empty list of strings;
- as in `run_limits`: `budget_usd` a number > 0 and ≤ 1000, `max_minutes` > 0 and ≤ 1440 (no
  booleans, strings, `null`, NaN or infinity); `max_tokens_total` an integer 1–100 000 000;
  `max_unmetered_calls` an integer ≥ 1;
- `debrief_budget_usd`, `debrief_max_minutes`: as in [`debrief_limits`](#debrief_limitsa);
  `debrief_max_tokens`: as in [`debrief_max_tokens`](#debrief_max_tokensa);
- `allow_unmetered`: a bool.

| Key | Default | Meaning |
|---|---|---|
| `repo_roots` | `[]` | Directories whose git repositories are scanned |
| `include_worktrees` | `false` | Also scan linked git worktrees |
| `servers` | `[]` | `hosts.conf` names audited each day |
| `fail_on` | `"high"` | Lowest severity that makes the run exit 1 |
| `include_dev` | `false` | Dev-only dependencies count toward `fail_on` |
| `cooldown_days` | `14` | A fix release must be at least this old (Rule S-10). The executor never goes below 14 |
| `local_runtimes` | `true` | Check Node.js, PHP and Python on this machine |
| `fix_mode` | `"plan"` | `plan` writes a fix plan per git repository with fixable findings, `fixplan-<name>-<8 hex of its path>.md`, naming the repository as the `stage-fix` target (a `repo_roots` folder holding several repositories gets one plan each; a nested repository is left out of its parent's plan); `apply-worktree` stages fixes in a worktree per repo (without agents), off `origin/HEAD` (nothing is staged without it), with the cooldown clamped to at least 14 days. The result must pass [`validate_diff`](#validate_diffwt-base) or the worktree is removed; a same-day worktree is reused only when it is a worktree of that repository on its `vulnscan/<date>` branch with no other commits or changes |
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
| `team_dir` | `""` | Root of the generated goose team's project (it contains `.goose/recipes/`). Required by agent runs, `agent-selftest` and `agent-chat`. Must be an existing directory you own, not group/world-writable; not home, an ancestor of home, or at, inside or above `~/.config/agentteams`, the vulnscan config or state directory, or a credential directory (so at, inside or above `~/.config/agentteams/keys` is always refused); and must not contain `key_file`, nor the operator signing key when `AGENTTEAMS_DECISION_ED25519_KEYFILE` is set (a key kept elsewhere is checked only then) |
| `recipe` | `".goose/recipes/vuln-remediation.yaml"` | Recipe the container runs, relative to `team_dir`; its prompt must carry the `task` parameter (the generated orchestrator's opens with a role check, so a one-shot run of it stops after answering that) |
| `model` | `"z-ai/glm-5.3"` | The one model the gateway allows (an OpenRouter model id) |
| `upstream` | `"http://host.docker.internal:8791"` | Where the gateway sends requests. Without a local route proxy, use `"https://openrouter.ai"` |
| `key_file` | `""` | File holding `OPENROUTER_API_KEY=...` (or the bare key). Mounted read-only into the gateway only |
| `budget_usd` | `2.0` | Spend cap per run (see [What leaves your machine](#what-leaves-your-machine)) |
| `max_tokens_total` | `10000000` | Prompt + completion token cap per run, independent of reported cost (integer 1–100 000 000) |
| `max_unmetered_calls` | `20` | The gateway refuses after this many successful replies without usable (non-zero) usage or cost (integer ≥ 1). Upstream errors (4xx/5xx replies, and calls that got no reply: DNS failure, connection refused, timeout) are counted separately (`upstream_errors` in `spend.json`) and do not count here |
| `allow_unmetered` | `false` | Accept replies without usage (then only the token cap, if usage has tokens, and the time cap bound a run) |
| `max_minutes` | `45` | Time cap per run; the container is removed at the cap |
| `image` | `""` | Image tag; default `agentteams-vulnscan-agent:<agentteams version>` |
| `command` | goose `run --recipe {recipe} --name {session} --with-extension "python -m agentteams.vulnscan.mcp" --params task={task}` | Command run in the container |
| `debrief_budget_usd` | `0.5` | Spend cap for `agent-chat` |
| `debrief_max_minutes` | `20` | Time cap for `agent-chat` |
| `debrief_max_tokens` | `400000` | Prompt + completion token cap for `agent-chat` (integer 1–100 000 000) |

---

## Command reference

All commands are `python -m agentteams.vulnscan <command>`. Every command except `daily` and
`schedule` first warns when the last daily run is stale.

| Command | Purpose |
|---|---|
| `scan PATH` | Scan manifests under a repo or a folder of repos |
| `inventory LOG --host H` | Analyse a server-kit audit log |
| `runtimes` | Node.js, PHP and Python on this machine |
| `fix PATH [--apply]` | Plan, or make, dependency fixes in a working tree you control. git runs hardened (no hooks, fsmonitor, system or global config); `--apply` refuses a tree whose own git config defines a filter driver, or that git refuses to open (e.g. `safe.directory`: "git refuses to read this repository (...) — fix it by hand") |
| `init [--repo-root DIR]… [--server NAME]…` | Write a starter config (never overwrites) |
| `daily` | One full run (scan; with agent mode, the agent run and publishing) |
| `schedule install\|remove\|status [--apply] [--hour H] [--minute M]` | Manage the daily job (launchd on macOS, else a systemd user timer, else cron). Dry run without `--apply` |
| `server-kit [DEST]` | Export the server scripts (keeps an existing `hosts.conf` / `apps.conf`) |
| `grant sign\|list\|revoke` | Standing remediation grants ([Grants](#grants)) |
| `act OP TARGET [--rationale R]` | Run one grant-covered action yourself, through the executor |
| `serve-requests` | Serve the spooled requests once. Refuses while a run holds the run lock; has no run, so it refuses `report` requests |
| `review [--since DATE] [--gh-replies]` / `review --ack [--note N]` | After-action review, with an overdue notice when actions have gone unacknowledged for more than 3 days ([`overdue`](#overduedays-rows)); `--ack` records that you reviewed it |
| `agent-image build --goose-version V --goose-sha256 S [--python-image IMG] [--source DIR]` | Build the agent image from an agentteams source checkout |
| `agent-selftest` | Prove the container's isolation for the current image ([checks](#selftestcfg)) |
| `agent-run` | One agent run on the latest scan |
| `halts` | Open escalations and decision-log HALTs, with how to clear each |
| `resolve ID --evidence TEXT [--key-id ID]` | Resolve an escalation ([Escalations](#escalations)) |
| `say TEXT [--target T] [--days N]` · `say --from-gh REF [--repo O/R]` · `say --list` · `say --remove ID` | Operator notes for the next run |
| `publish-grant sign\|list\|revoke` | The grant that lets the executor push reports. `sign` checks `--node-id` and `--owner-id` first (exit 2): `refused: --node-id is empty — run the gh lookup first; it must be the ops repository's node id (gh repo view <owner/repo> --json id)`, `refused: --owner-id is empty — run the gh lookup first; it must be your numeric GitHub user id`, `is required` in place of `is empty` when the flag is omitted, and `refused: --owner-id must be your numeric GitHub user id (got '…')` |
| `publish [--run DATE]` | Publish a run's files now |
| `agent-chat [--run DATE]` | Resume a past run's goose session interactively |

The signing commands (`grant sign`, `publish-grant sign`, `resolve` for serious kinds) also accept
`--allow-checkout-signing` and `--confirm-review-sha256` ([Signing](#signing)). `DATE` is always
`YYYY-MM-DD`.

**The scheduled job** runs `python -P -m agentteams.vulnscan daily`. Its `PATH` is built by
`schedule.job_path`: the directories `schedule.JOB_TOOLS` resolve to at install time plus
`schedule.SYSTEM_PATH`, leaving out relative entries, a literal `~` and any directory inside a git work
tree under `$HOME`; `install` adds a note naming any tool left out. Besides `PATH` it gets only `HOME`,
`XDG_CONFIG_HOME`, `XDG_STATE_HOME`,
`AGENTTEAMS_VULNSCAN_CONFIG_DIR`, `AGENTTEAMS_VULNSCAN_STATE_DIR`, `AGENTTEAMS_SERVER_KIT_DIR` and
`AGENTTEAMS_SERVER_KIT_LOGS`, as set when you install it (`schedule.JOB_ENV_VARS`). No other
`AGENTTEAMS_*` variable is baked in: the plist, unit or crontab line is plain text, and such
variables can hold API keys. A baked value holding a control character is refused
(`schedule.job_environment` raises `ValueError`); `install` reports it as a plan error and writes
nothing. `--hour` must be 0–23 and `--minute` 0–59, or nothing is written. A
`%` is escaped in the crontab line and the systemd unit. Under systemd, `schedule remove` reports a
failed `systemctl disable` only when the units were installed.

### Exit codes

| Command | 0 | 1 | 2 | 3 | Other |
|---|---|---|---|---|---|
| `scan`, `inventory`, `runtimes`, `daily` | clean | findings at or above `--fail-on` (`daily`: the config's `fail_on`) | usage or setup error: a missing `scan` path or `inventory` log; `daily`: no config | partially unscanned (`daily`: also another daily run holds the lock, or the summary or heartbeat could not be written) | 4: malicious package or compromise indicator · `daily`: 143, SIGTERM during the agent phase (both containers removed) |
| `fix` | nothing held back | something held back or refused | usage error, a missing path | a feed failed (partially unscanned) | 4: a malicious pin |
| `schedule` | done or dry run | errors (a time out of range, a failed command) | the config is unusable | — | — |
| `grant sign` | signed and installed | not signed (declined, preflight) | refused (bounds; does not verify) | — | — |
| `grant revoke`, `publish-grant revoke` | revoked | no such grant | — | — | — |
| `act` | done | refused | no config | failed | — |
| `serve-requests` | served | — | run lock held, or no config | — | — |
| `agent-image build` | built | build failed (also: Docker not reachable, checked before `pip wheel`) | no config | — | — |
| `agent-selftest` | passed | failed, refused (also: Docker not reachable or the image not built), or a `docker` command failed part-way (`agent-selftest failed: …`) | no config | — | — |
| `agent-run` | completed | — | no scan, or no config | not run, error or timed out | 143: SIGTERM (both containers removed, `agent-error` recorded) |
| `halts` | nothing blocks | something blocks, or the ledger fails verification | an unusable config | — | — |
| `resolve` | resolved | not resolved (declined, not signed) | refused | — | — |
| `say` | done | no such note | refused | — | — |
| `publish-grant sign` | signed and installed | not signed | refused (also: `--node-id` / `--owner-id` empty, missing or not numeric) | — | — |
| `publish` | published | no publish grant | no such run, or no config | failed or refused | — |
| `agent-chat` | the session's exit code | | refused | | 75: run lock held · 124: time cap · 143: SIGTERM (both containers removed) |

"No config" includes a config that cannot be used (invalid JSON, not an object, a key with a wrong
value): every command that reads the config then exits 2 and prints the reason. Without a config
`halts` still runs (HALTs are read from the grants' governance directories only, without the
config's `governance_dir`) and exits 0 or 1 as above; with an unusable one it exits 2. Argument errors exit 2, including a `--since`
(`review`) or `--run` (`publish`, `agent-chat`) that is not a `YYYY-MM-DD` date.

`init`, `server-kit`, `review` and every `list` otherwise exit 0.

### JSON output

`scan`, `inventory` and `runtimes` with `--format json`, and each run's `summary.json` and
`repos-*.json`, print `report.Report.to_dict()`: one object.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | `1` (`REPORT_SCHEMA_VERSION`; the string `"1"` before this release). It increases on an incompatible change: check it before reading the rest. `Report.from_dict` ignores unknown top-level keys |
| `target` | string | the scanned path, host label, `this-machine` for local runtimes (`engine.LOCAL_TARGET`), or `"daily"` for the daily aggregate |
| `findings` | array of Finding objects | below |
| `unscannable` | array of `{"path": string, "reason": string}` | manifests without exact versions |
| `feed_errors` | array of strings | feeds or hosts that failed; non-empty means partially unscanned (exit 3) |
| `checked` | integer | items checked (distinct package versions plus runtimes) |
| `generated_at` | string | UTC ISO-8601 time, whole seconds |

A Finding (`report.Finding`):

| Field | Type | Meaning |
|---|---|---|
| `kind` | string | `package`, `runtime`, `host` or `ioc` |
| `target` | string | repository (relative to the scan root), host name, or `this-machine` for local runtimes (`engine.LOCAL_TARGET`) |
| `name` | string | package or runtime name; `host` / `compromise indicator` for host checks |
| `version` | string | installed version (`""` when not applicable) |
| `ecosystem` | string | OSV ecosystem (`PyPI`, `npm`, …); `""` for runtimes and host checks |
| `scope` | string | `runtime` or `dev` |
| `severity` | integer 0–4 | 0 unrated, 1 low, 2 moderate, 3 high, 4 critical (`osv.SEVERITY_NAME`). A package advisory without a severity label counts as 2; a malicious package or `ioc` is 4 |
| `fix` | string | smallest version that fixes every advisory with a fix; `""` when none is known |
| `unfixed` | integer | advisories with no fixed version |
| `ids` | array of strings | advisory ids (GHSA, PYSEC, CVE, `MAL-*`, …) |
| `locations` | array of strings | manifest paths or install locations |
| `malicious` | boolean | an OSV `MAL-*` record matches (exit 4) |
| `distro` | boolean | a Python package in an OS-managed location (ignored by `--fail-on`) |
| `detail` | string | host-check or runtime message, or the malicious-package note |

---

## The agents' interface: MCP tools

The `vulnscan` MCP server (`python -m agentteams.vulnscan.mcp`, stdio) runs inside the agent
container. Everything it returns that came from advisories or repositories is prefixed as **data**
(C-4).

| Tool | Arguments | Effect |
|---|---|---|
| `get_summary` | — | Today's `summary.md` |
| `list_findings` | `min_severity` (`critical`…`any`), `target` | Findings as JSON, malicious first, then by severity; at most 500 rows, with `total` (rows that matched) and `truncated` |
| `get_fix_plans` | — | The deterministic fix plans, one per git repository, each headed by the repository path to give `stage-fix` as `target` |
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
refuses the request, and so does an unreadable `*.json` file in either grants directory ("cannot
tell which repositories are protected").

**Directories and keys** (`grant.dir_problem`, `verify_key`). Whoever can write these can plant or
swap a grant or a key, so each is judged by its own final component (`lstat`, `O_NOFOLLOW`):

- a grants directory (`vulnscan-grants/`, `vulnscan-publish-grants/`) or the verify-keys directory
  that is a symlink, is not a directory, or is not owned by you or is group/world-writable is
  refused. A refused grants directory refuses every grant of that kind; a refused verify-keys
  directory fails every verification;
- each grant file must be a regular file you own, not group/world-writable;
- the verify-key file must be a regular file (not a symlink) you own, not group/world-writable,
  and UTF-8 text.

Group- or world-*readable* is fine.

### Signing

Every signature goes through one flow (`grant.operator_sign`):

1. **Preflight, key unread.** `AGENTTEAMS_DECISION_ED25519_KEYFILE` must name your private key
   file; the signing code's integrity is checked; the install-location check
   (`signer_location.verify_install_location`) refuses an `agentteams` an agent could edit: the
   package, a venv's `sys.prefix` or the interpreter inside the current directory, a git work tree
   or an agent-writable `allowWrite` root. The base interpreter (`sys.executable`, and `sys.prefix`
   outside a venv) counts as inside a work tree only when git does not ignore its path there, so
   Homebrew's Python (in the git-ignored `Cellar/` of Homebrew's own work tree) and a `pipx` install
   on it pass. Only the work tree's own ignore rules count: the lookup drops inherited `GIT_*`
   variables and global/system git config (`core.excludesFile`). A project `.claude/settings.json`
   that exists but cannot be read or parsed (or whose `allowWrite` is not a list) refuses signing:
   the agent-writable roots it grants are unknown. A missing file or no sandbox block means none. The package and a venv's prefix are judged strictly: a `pip install` into Homebrew's
   Python without a venv is still refused.
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

### `agentteams.vulnscan.engine`

#### `engine.scan_path(root, *, include_worktrees)`

Scan every manifest under `root` (a repository or a folder of repositories). Returns a
[`Report`](#json-output) whose targets are repositories relative to `root`. A feed failure lands
in `feed_errors`; it never raises for one.

#### `engine.analyze(deps, report, *, distro_check)`

Look up `(target, Dep)` pairs in OSV and append findings to `report` in place. Feed failures go to
`report.feed_errors`. `distro_check=True` marks Python packages in OS-managed locations `distro`.

#### `engine.scan_inventory(text, target)`

Analyse a server-kit audit log (`INV`, `RUNTIME`, `HOSTCHECK`, `IOC` lines; anything else is
ignored). Returns a `Report` for host `target`.

### `agentteams.vulnscan.report`

#### `Report`

Dataclass: `target`, `findings`, `unscannable` (`(path, reason)` tuples), `feed_errors`, `checked`,
`generated_at`. `exit_code(fail_on, include_dev)` gives the shared exit code; `to_dict()` /
`from_dict(data)` convert to and from the [JSON shape](#json-output); `markdown(new_keys)` renders
`summary.md`; `merge(other)` folds another report in.

#### `Finding`

Dataclass with the fields listed under [JSON output](#json-output). `key()` is the identity used for
NEW marks: `target|kind|ecosystem|name|version|ids or detail`.

#### `report.new_finding_keys(current, previous)`

The `Finding.key()` values present in `current` but not in `previous` (every key when `previous` is
`None`).

### `agentteams.vulnscan.osv`

#### `osv.vulnerable_ids(items)`

`{(ecosystem, name, version): [advisory ids]}` for the affected items only, through OSV
`querybatch`. Raises `FeedError` (`agentteams.vulnscan._http`) on a malformed or incomplete reply
or endless pagination: nothing is concluded about the items.

#### `osv.details(vuln_id)`

The full OSV record (cached per process). Raises `FeedError`.

#### `osv.fix_for(record, ecosystem, name, version)`

The `fixed` version of the affected range that contains `version`, or `None` when that range has no
fix. `GIT` ranges are ignored.

### `agentteams.vulnscan.fix`

#### `fix.fix_path(root, *, apply, allow_major, cooldown_days, include_worktrees, allow_dirty, npm, manifests)`

Plan (with `apply=True`, make) dependency fixes under `root`. Defaults: `apply=False`,
`allow_major=False`, `cooldown_days=14`, `include_worktrees=False`, `allow_dirty=False`, `npm=True`,
`manifests=None` (every manifest `discover` finds; `daily` passes one repository's). A dry run lists
each npm lockfile's vulnerable packages through `plan_npm_lock` whether or not `npm` is set; `apply`
never does (there npm alone decides). Returns a `FixResult`. Symlinked manifests are refused, never followed.

#### `fix.plan_npm_lock(path, *, cooldown_days, label)`

`(rows, note)` for one `package-lock.json` / `npm-shrinkwrap.json`, from OSV; npm is not run. One
`PinChange` per vulnerable package version, with `new` the smallest version the advisories call fixed.
`stage-fix` runs `npm audit fix` on the lockfile as a whole, and npm picks the versions, so `new` is a
floor, not a promise (the plan shows it as `≥`). Actions follow `plan_requirements`, with npm's limits:
`malicious`; `no-clean-release` (no advisory names a fix); `major-held` when the fix leaves the
installed version's caret range (`1.x` → `2.x`, `0.3.x` → `0.4.x`), whatever `allow_major` says,
because npm without `--force` never takes it; `workspace-held` when the lockfile's root package or
the `package.json` beside it declares `workspaces` (`stage-fix` fails closed there);
`npm-unavailable` when npm is not on `PATH`. Dev-only packages are planned like the rest (npm fixes
them too) and say `dev only` in the note. Raises `FeedError`.

#### `FixResult`

Dataclass: `changes` (`PinChange` list), `npm_plan` (`PinChange` list from `plan_npm_lock`),
`npm_notes` (lockfile → note), `npm` (`(directory, summary)` tuples), `composer`, `refused`
(`(path, reason)` tuples), `feed_errors`, `applied`, `target` (the repository a daily plan is for)
and `target_problem` (why `stage-fix` cannot take it). `exit_code()`: 4 a malicious version,
3 a feed failed, 1 anything held back or refused, else 0. `markdown()` renders the fix plan; with
`target` set it opens with ``Repository (the `stage-fix` target): `<path>` ``.

#### `PinChange`

Dataclass for one requirements edit or npm plan row: `path`, `name`, `old`, `new`, `action` (`fix`,
`major-held`, `no-clean-release`, `cooldown-held`, `hash-held`, `malicious`; npm rows also
`workspace-held`, `npm-unavailable`), `ids`, `note`.

### `agentteams.vulnscan.ledger`

#### `ledger.read(*, verify)`

Every row in order. With `verify=True` (the default) checks each MAC, the chain and the high-water
mark, and raises `ValueError` on any break, a missing ledger that once had rows, or an incomplete
final row. Row kinds: those in [Ledger rows](#ledger-rows) (`begun`, `done`, `failed`, `refused`,
`escalation`, `resolved`, `notes-folded`, `publish-begun`, `publish-done`, `publish-failed`, `ack`).

#### `ledger.uses(grant_id, rows)`

How many uses are charged to a grant: its `begun` rows. `rows` reuses rows already read.

### `agentteams.vulnscan.schedule`

#### `schedule.install(hour, minute, *, apply)`

Install the daily job (launchd, else a systemd user timer, else cron). Returns a `Plan` (`files`,
`commands`, `notes`, `errors`); without `apply=True` nothing is written or run. A bad time or a
control character in a baked variable is a plan error, and nothing is written.

#### `schedule.remove(*, apply)`

Remove the daily job. Returns a `Plan`.

#### `schedule.status()`

Text: the mechanism, whether the job is installed, the config and state paths, and the heartbeat.

#### `schedule.job_environment()`

The variables baked into the job: `HOME` and those of `JOB_ENV_VARS` set now, with `PATH`
always replaced by `job_path()[0]`. Raises `ValueError` for a value holding a control character.

#### `schedule.job_path(path)`

`(PATH, skipped)`: the `PATH` baked into the job, built from `path` (default: this process's
`PATH`). It holds the first safe directory each of `JOB_TOOLS` (`git`, `gh`, `docker`, `ssh`, `npm`,
`node`, `openssl`) resolves to, then `SYSTEM_PATH` (`/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`). A
directory that is relative, holds a literal `~`, or lies inside a git work tree under `$HOME` is left
out; `skipped` lists `"tool (dir)"` for each tool found only in a left-out directory.

### `agentteams.vulnscan.gateway`

#### `gateway.main(argv)`

The egress gateway, `python -m agentteams.vulnscan.gateway`. `agent_run.gateway_argv` sets every
flag; you never run it by hand.

| Flag | Default | Meaning |
|---|---|---|
| `--listen HOST:PORT` | `0.0.0.0:8080` | where it listens |
| `--model M` | required | the only model relayed |
| `--key-file PATH` | required | file holding `OPENROUTER_API_KEY=...` or the bare key |
| `--upstream URL` | `https://openrouter.ai` | http(s) URL requests go to |
| `--budget-usd X` | `2.0` | spend cap; finite and ≥ 0 |
| `--spend-file PATH` | none | where `spend.json` is written |
| `--max-tokens-total N` | `0` | prompt + completion token cap; 0 means none (runs and debriefs always pass a positive cap) |
| `--max-unmetered-calls N` | `20` | refuse after this many replies without usable usage or cost; ≥ 1 |
| `--allow-unmetered` | off | never refuse for unmetered replies (they are still counted) |

A bad flag value exits 2; so does a key file that cannot be read or holds no key.

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

#### `open_pr(repo, rationale)`

Push the staged `vulnscan/*` branch as a new ref and open the pull request; called by `act` for
`open-pr`. Idempotent:

- When the remote branch exists and has an open pull request from the same repository, that pull
  request is returned (`existing: true`), but only if its branch holds the staged tree; otherwise it
  refuses (`not claiming it`). Pull requests from forks with the same branch name are ignored.
- When the remote branch exists with no open pull request, one is created without pushing, but only
  if the remote ref is exactly the staged commit (a push that succeeded before `gh pr create`
  failed); otherwise it refuses (`not overwriting`).
- Once the pull request exists, the executor's worktree and staged state are removed; the local
  branch is kept. A second call then raises `no staged fix`.

Re-staging the same change on the same day releases the earlier local branch only when the executor
created it and every worktree holding it is executor-owned; otherwise `stage-fix` refuses. When
`stage-fix` fails it removes its worktree and, if this attempt created the branch, deletes that
branch while it still points at the base commit and is checked out nowhere outside the executor's
worktrees, so a retry starts clean. `stage-fix` stages only the validated paths, as literal
pathspecs: tracked files with `git add -u` (so a tracked pin file under a gitignored directory is
staged without `-f`) and new, non-ignored files with a plain `git add`. It refuses if anything else
ends up staged. Raises `ExecError`.

#### `validate_diff(wt, base)`

Check a staged change against `base` before `stage-fix` commits it, and before `daily` keeps an
`apply-worktree` result. Returns the changed paths; raises `ExecError` on anything else:

- only requirements files and npm lockfiles may change or appear, as regular, non-executable files;
  renames, deletions, mode changes and symlinks are refused;
- a requirements pin may change only its version, upward, to a final release; every other byte of
  the file stays (`manifests.check_pin_edit`). A file with `--hash` options is never edited (`fix`
  holds it back as `hash-held`);
- every lockfile entry must resolve over `https://` to the registry allowlist, or have no
  `resolved` and a plain version: an entry with a URL or path in `version` and no `resolved` (a v1
  lockfile form) is refused. A workspace `link` or `file:` entry passes only when it is inside the repository and
  unchanged from `base`. A change to the root package's dependencies, `scripts` or `workspaces` is
  refused (`manifests.check_lock_edit`).

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

`(valid grants, problems)`. A grant file that is not a regular file (a symlink included), is
not owned by you, or is group/world-writable is rejected and listed in `problems`. More than two valid
grants refuses all of them.

#### `covers(record, op, target)`

Whether the grant's scope includes `op` on `target` (use counts are the ledger's).

#### `verify_key(key_id)`

The public key PEM from `~/.config/agentteams/verify-keys/<key-id>.pub.pem`, refused when the
directory fails [`dir_problem`](#dir_problemdirectory), or the file is a symlink, not a regular file,
not yours, or group- or world-writable. Raises `GrantError`.

#### `dir_problem(directory)`

Why a grants or verify-keys directory may not be trusted (`is a symlink — refused`, `is not a
directory — refused`, `is not owned by you or is group/world-writable — refused`), or `None`.

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

`(valid publish grants, problems)`. Each file gets the same per-file checks as
[`grant.load_active`](#grantload_active-now). More than one valid grant refuses all of them.

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
`agent-error` rather than raising. Exception: when another run or a debrief holds the run lock, it
returns `not-run` with `status_written: False`, and nothing in the run directory is written or
finalized (the holder may be a live run of that directory); the same holds when an earlier run's
record cannot be archived. Every other not-run reason is decided under the lock. The status also
carries `report` (`{"request_id", "submitted"}` of the report this run submitted, or `null`) and,
when an earlier run's record was moved aside, `archived_previous` (its name under
`<run>/agent/runs/`). A run whose agent exits 0 without submitting a report of its own is
`agent-error`, not `completed`.

#### `fold_notes(task, run_id)`

Fold the active notes into the task and ledger which ones went in.

#### `selftest(cfg)`

Run the isolation self-test container (with the gateway it needs) and record the result against
the image id and `flags_sha256` (a hash of the container and gateway flags and the self-test
itself). Refuses without `agent.key_file` or `agent.team_dir`, or with a `team_dir` that fails
`team_dir_problem` (the same check a run makes); there is no fallback directory. It also refuses,
before starting any container, when Docker is not reachable or the image is not built
(`checks.docker_reachable: false`; the CLI prints
`agent-selftest: docker is not reachable or the agent image <tag> is not built — start Docker (or run agent-image build) first`
and exits 1). A `docker` failure part-way through raises; the CLI prints
`agent-selftest failed: <error>` and exits 1. Every check must be `true`:

| Check | Meaning |
|---|---|
| `no_credential_paths` | no `~/.ssh`, `~/.config/gh`, `.git-credentials`, `.netrc`, `.docker`, `.npmrc`, `.pypirc`, `.aws`, `.gnupg`, `.config/agentteams/keys` |
| `no_token_env` | no `GH_TOKEN`, `GITHUB_TOKEN`, `SSH_AUTH_SOCK`, `AWS_ACCESS_KEY_ID`; the model key is the dummy |
| `internet_blocked` | neither example.com nor OpenRouter is reachable directly |
| `gateway_reachable` | the gateway answers on the internal network |
| `readonly_rootfs`, `readonly_run_dir`, `readonly_results` | the root filesystem, run directory and results are read-only |
| `spool_writable` | the agent can queue requests |
| `goose_dirs_writable` | goose can create its log, session and config directories (`XDG_STATE_HOME` is `<sessions mount>/state`, so goose's logs survive as `<run>/agent/goose-data/state/goose/logs`; not under the root-owned `.local` the sessions mount creates); without them goose stops before its first model call |
| `no_capabilities`, `no_new_privs`, `non_root` | no Linux capabilities, no privilege gain, a non-root uid (the image runs as 10001) |

#### `selftest_ok(cfg)`

`(ok, reason)`: whether a passing self-test exists for the current image and the current container
flags (`flags_sha256`). When Docker cannot be used the reason is `docker is not reachable or the
agent image <tag> is not built — start Docker (or run agent-image build) first, then re-run
agent-selftest`.

#### `docker_unreachable(tag)`

Why Docker cannot be used, or `""`. With `tag=None` it checks only that the daemon answers
(`agent-image build` calls it before `pip wheel`, and fails with
`agent image build failed: docker is not reachable — start Docker (or install it) first`); with a
tag, also that the image exists. Every probe gives up after 60 s: a hung docker client counts as
not reachable (it never blocks for the 10-minute default, and never raises).

#### `validate_agent(a)`

Check every `agent.*` value's type and range ([the rules](#configuration-vulnscanjson)); raises
`ValueError` naming `agent.<key>`. `daily.load_config` calls it, so a bad value is refused at load.

#### `team_dir_problem(path, secrets)`

Why `path` may not be mounted as the team directory, or `None`. Always refuses a path at, inside or
above `~/.config/agentteams` (so its `keys/`), home or an ancestor of it, and the vulnscan config and
state directories. `secrets` (`agent.key_file`) must lie outside it; the signing key named by
`AGENTTEAMS_DECISION_ED25519_KEYFILE` is added only when that variable is set.

### `agentteams.vulnscan.selftest_probe`

The in-container probe the [self-test](#selftestcfg) runs with the agent's exact flags. It prints one
line, `SELFTEST {json}`, with one boolean per check; its text is part of `flags_sha256`.

`PROBE_TEMPLATE`: the probe's Python source, with `%s` / `%d` for the gateway host name and port.

#### `selftest_probe.render(gateway, port)`

The probe source for one gateway: `PROBE_TEMPLATE` filled with the gateway container name and port.

### `agentteams.vulnscan.debrief`

#### `run_debrief(cfg, run_dir)`

Resume a past run's goose session with the terminal attached, a read-only spool, and its own
gateway with the debrief caps.

All three debrief limits are checked before anything starts: a bad one returns 2 with
`debrief refused: agent.debrief_budget_usd must be …` (or the key at fault). The config load
normally refuses it first ([`validate_agent`](#validate_agenta)).

A SIGTERM during the debrief removes both containers, records `terminated` in the debrief's
`status.json` and exits 143.

#### `debrief_limits(a)`

`(budget_usd, max_minutes)` from `agent.debrief_budget_usd` and `agent.debrief_max_minutes`, by the
run limits' rules: NaN, infinity, booleans, strings and `null` raise `ValueError`.

#### `debrief_max_tokens(a)`

The debrief's token cap from the agent settings `a` (as returned by `agent_run.agent_cfg`):
`debrief_max_tokens`, default 400 000. Anything but an integer 1–100 000 000
(`agent_run.MAX_TOKENS_TOTAL_CEILING`) raises `ValueError`; booleans, floats and strings included.

### `agentteams.vulnscan.daily`

#### `run_daily(cfg, *, today)`

One daily run under `<state>/daily.lock`. Returns `(report, exit code, run directory)`; while
another run holds the lock, exit code 3 and nothing is scanned or written.

#### `publish_run(cfg, run_dir, run_id)`

Publish a run when a publish grant is active; `None` otherwise.

#### `load_config(path)`

The config with defaults filled in. Raises `FileNotFoundError` when there is none, and
`ValueError` naming the file and the key when it is not a JSON object or a key has a wrong type or
range, every `agent.*` key included ([`validate_agent`](#validate_agenta)).

### `agentteams.vulnscan.review`

#### `review.build(*, since, for_publish, gh_replies)`

Render the after-action review. `for_publish=True` puts every agent- or repository-derived text
inside a fence and scrubs local identity; `gh_replies=True` lists replies on the agents' pull
requests (it calls `gh`).

#### `acknowledge(note)`

Write the `ack` row that closes the current review window.

#### `overdue(days, rows)`

Whether actions have gone unreviewed for more than `days` (default 3), or the ledger cannot be read or
verified. `rows` reuses ledger rows already read; by default the ledger is read and verified. `review`
prints an "after-action review overdue" notice when it is true.

### `agentteams.vulnscan.report_store`

#### `save(run_dir, markdown, *, request_id)`

Store the agent's report fenced and inert under `<run>/agent-report/report.md`.

#### `finalize(run_dir, run_state, *, since)`

Record the run's final state in the stored report's header and return its header fields. With
`since` (the run's start), a report submitted earlier is an earlier run's: it is left alone and
`None` is returned. `None` also when no report was submitted.

#### `archive(run_dir, name)`

Rename the stored report to `<name>.report.md` beside it (an earlier run's report, kept).

#### `render_for_review(run_dir)`

The review section for the stored report, bound to the latest agent run: a report submitted before
the run in `<run>/agent/status.json` started is not shown, and the section says that run submitted
none.

### `agentteams.vulnscan.run_records`

#### `archive_previous(run_dir)`

Before an agent run writes anything (under the run lock), move the previous run's
`agent/status.json`, `agent/transcript.log` and `gateway/spend.json` to
`<run>/agent/runs/<previous run id>/`, and its report to `agent-report/<previous run id>.report.md`.
The latest run therefore always owns `agent/status.json`, `agent/transcript.log` and
`agent-report/report.md`. Returns the archive name, or `None` when there was nothing to move.

#### `run_dirs_of(run_dir)`

The archived run records of a run directory, oldest first (listed by `review`).

### `agentteams.vulnscan.notify`

#### `send(cfg, key, **fields)`

Deliver one fixed notification. Returns whether a notifier ran. It never raises: an unsafe field,
a malformed `command:` or a notifier that cannot start or times out is reported on stderr. On macOS the text is passed
to `osascript` as an AppleScript string literal, so non-ASCII characters show correctly.

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
