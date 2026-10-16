# Migration Guide

This guide covers two migrations: upgrading a team to agentteams 1.0 (and rolling back), and bringing a team
generated before section fencing under merge-safe management (from [When to Use This Guide](#when-to-use-this-guide)
on).

---

## Upgrading to 1.0 (from rc.9 or earlier)

### Who this is for

- **Consumers:** operators of agent teams that agentteams generated, upgrading from `1.0.0-rc.9` or older.
- **Maintainers:** anyone running `agentteams --self` in the agentteams repository.

Most of the upgrade is a normal re-render. Three things need attention first:

- the decision log, which 1.0 reads more strictly in one place and more leniently in another;
- the gate hook's interpreter;
- one removed research flag.

Limitations that ship with 1.0 are listed in [Known Limitations in 1.0](known-limitations.md).

### What changes for you in 1.0

| Change | What you do | Source |
|---|---|---|
| **A HALT now blocks every action named as a token in its id,** not only actions its id starts with. An older, unretracted HALT such as `m6-overwrite-render-…` or `overwrite post-render closing check` starts blocking `overwrite`. | Step 1: review the report, and retract what no longer applies. | #225 |
| **Signed decision logs chain.** `--sign-decision` now fills `prev_digest`. Logs that rc.9 left with empty links verify through a *legacy prefix*. `--verify-integrity` exits 1 on a broken chain. | Step 1 | #224 (338) |
| **`--migrate-decision-log` pads short legacy rows,** so ordinary governance appends accept a migrated log. The earlier promise that every data line stays byte-identical is replaced: each line keeps its old bytes as a prefix. | Step 2, if your team has a legacy log or one migrated before 1.0 | #224 (399/406) |
| **The gate hook runs under a pinned interpreter.** Each render writes `<hooks>/agentteams-python`, an untracked file naming the rendering interpreter. When `python3` cannot import agentteams, the gate re-runs under the pin. With no working interpreter it now asks or denies; before, it silently allowed. | Steps 3 and 4 | #226 |
| **BREAKING: `python -m agentteams.research browser --screenshot` is removed.** | Step 5 | #224 (378) |
| **Backup sidecars are inert.** `<rel>.lost.<sid>.md` becomes `….lost.<sid>.md.agentteams-bak`, and existing sidecars are renamed on the next backup run. | Nothing: step 3 does it. | #224 (377) |
| **A corrupt or schema-invalid vulnerability cache reads `stale`,** never `fresh` (new optional `freshness.cache_corrupt`). | A run that passed the freshness gate on a broken cache now stops. Refresh the cache, or use a signed waiver. | #224 (433) |
| **`--interop-from` never widens a grant.** Projecting a narrower-than-default agent onto Codex (outside its role gate), Grok or agents-md is refused unless the agent is named in `--interop-allow-cooperative`. A pinned sync to Goose needs the pinned readfs server at the project root. | Name agents explicitly where a cooperative grant is acceptable. | CHANGELOG `[1.0.0-rc.10]` |
| **`--framework goose` no longer overwrites a repo-root `AGENTS.md` it did not write.** | To hand the file to goose, delete `AGENTS.md` and re-render. | CHANGELOG `[1.0.0-rc.10]` |
| **Grok: scoped entries and `MCPTool` are dropped from `tools:`.** `--update --merge` names any existing `.grok/agents` file that still carries one. | Run `--reconcile-front-matter --reconcile-apply`, or fix the named files. | CHANGELOG `[1.0.0-rc.10]` |
| **The memory index may be sharded** above 20 MiB (`memory-index.part-NNNN.json` plus a manifest). Earlier releases reject the manifest. | Once one writer is on 1.0, keep every writer of that index on 1.0 or later. | CHANGELOG `[1.0.0-rc.10]` |
| **An opt-in gate decision log** (`AGENTTEAMS_GATE_LOG`, plus an optional `AGENTTEAMS_GATE_LOG_KEY`). Unset, the hook behaves exactly as before. | Nothing, unless you want the log. | #217 |

### Upgrade steps

Run every command from the new 1.0 install (see [Moving to a release checkout](#moving-to-a-release-checkout-consumers)).
Use the same `--description`, `--framework` and `--output` you render with. Run steps 1 and 2 **before**
re-rendering, and before any agent session runs a gated action under 1.0. rc.9 has no HALT report, so step 1 needs
the 1.0 binary.

!!! warning "Editable installs switch at once"
    A team whose gate imports agentteams from an editable install gets the 1.0 gate behaviour (the HALT token rule
    and the chain check) as soon as that install changes, before any re-render. Run step 1 from a separate 1.0
    install **before** the editable install moves to 1.0.

#### Step 1 — Check the decision log and the HALTs: `--verify-integrity`

```bash
agentteams --verify-integrity --output <team dir>
```

Consumers point `--output` at the team directory. Maintainers of agentteams itself run the command from the
repository root, because the enforcement manifest is `references/enforcement-integrity.json` at the root.

Read two parts of the report.

- **The decision-log chain.** When the log has a `prev_digest` column, every row must link to the one before it.
    - A broken chain exits 1. Stop there and take it to @security; do not edit the log to make it pass. No
      re-chaining tool ships in 1.0.
    - A **legacy prefix** (a leading run of rows with empty links, accepted only when signing is active) is
      reported with its length and any HALT rows inside it. Deletions inside the prefix cannot be detected, so
      confirm those HALT rows are still present as recorded.
- **HALTs that newly block.** The report lists:
    - each unretracted HALT that newly blocks an agentteams gate action, with the **exact id** to retract it by;
    - every other unretracted HALT, because it blocks any action that your own `check_clearance` gates use and
      that its id names.

    This part is advisory and does not change the exit code. `--migrate-decision-log --dry-run` prints the same
    list.

**Retracting a HALT that no longer applies.** Append a `HALT-RETRACTED` row whose `action_reviewed` is the HALT's
exact id, as reported. It needs non-empty `evidence` (or `conditions`) saying why the HALT no longer applies. It
must authenticate like any other authorizing row in that log (signed, when the log is signing-active). A HALT
matched only by token is cleared **only** by its exact id: retracting the plain action (`overwrite`) does not
clear `m6-overwrite-render-…`. A retraction is a governance decision, so route it through @security as usual.

#### Step 2 — Upgrade or re-pad a legacy log: `--migrate-decision-log`

This step is needed when the team's `references/security-decisions.log.csv` is one of these:

- it still has the 7-column legacy header
  (`timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified,consumed`);
- it was migrated before 1.0 (full header, short rows).

Otherwise the command is a no-op.

```bash
agentteams --description <brief> --framework <fw> --output <team dir> --migrate-decision-log --dry-run
agentteams --description <brief> --framework <fw> --output <team dir> --migrate-decision-log --yes
```

(`--self --framework <fw>` in place of `--description` for the agentteams repository.)

- **What it does:**
    - It appends the missing signing columns to the header and pads each short row with empty trailing cells.
    - Each line keeps its old bytes as a prefix, and the migration asserts that no row's signed payload changes.
    - It backs up the log under `.agentteams-backups/` first.
- **It never adds `prev_digest`.** Chaining stays opt-in on that column, and legacy rows were never chained.
- **It refuses** a torn row and a value past the old header, and writes nothing.
- **It is an intended revocation.** The `signature` column turns signing on for the log, so every live unsigned
  `PASS` / `CONDITIONAL PASS` (unconsumed) and every unsigned `HALT-RETRACTED` stops clearing. The dry run lists
  them. Record new, signed clearances for what you still need. A HALT whose unsigned retraction is revoked is in
  force again, so re-run step 1 afterwards.
- **Clearance:** this is a governance-file edit and needs an ordinary @security PASS. Record that PASS in the
  repository's own log, not in the team log being migrated (the migration would revoke it).

#### Step 3 — Re-render

- **Consumers:** `agentteams --description <brief> --framework <fw> --output <team dir> --update --merge`
  (add `--yes` to apply non-interactively). Re-render from the 1.0 install: the gate's interpreter pin records
  **the interpreter that renders**.
- **The agentteams repository:** `agentteams --self --update --merge --yes`. That reinstalls the repository's own
  hook copies. `--verify-integrity` flags them until it has run.
- **Grok teams:** follow the `[GROK-SCOPE]` notices, then `--reconcile-front-matter --reconcile-apply` if needed.
  Re-adding capability keys is a C-3 change, so review each `[CAPABILITY GRANT CHANGED]` line.

The re-render installs the 1.0 gate and its pin, and renames old loadable backup files and sidecars.

#### Step 4 — Check the gate's interpreter: `--check-wiring`

```bash
agentteams --description <brief> --framework <fw> --output <team dir> --check-wiring
```

- **What it checks:** it prints the pin's value and resolved target, and says whether the gate would refuse the
  pin. The gate refuses a pin that is relative, that is not a `python*` executable, or that lies inside the
  project tree.
- **The import test:** it runs `<pin> -c "import agentteams"` and the plain `python3`.
- **On failure:** it exits non-zero when neither can import agentteams, and prints the fix:

    ```bash
    <python-with-agentteams> -c "import sys; print(sys.executable)" > <hooks>/agentteams-python
    ```

    Alternatively, re-render from an environment that has agentteams.
- **If the pin is missing:** an existing hooks `.gitignore` that belongs to you (no `agentteams-managed` marker,
  or a symlink) is left alone, and then the pin is not written either. `--check-wiring` tells you so.
- **Teams under `write_policy`:** the same command checks the live settings, the status channel and backup
  shadowing. It refuses Linux.

Then run `--verify-integrity` again. It should exit 0.

If neither the pin nor PATH's `python3` can import agentteams, the gate denies (confined) or asks (cooperative)
on every write it would check, and names `--check-wiring`. It never imports agentteams from the project tree.

#### Step 5 — Replace `--screenshot`

`python -m agentteams.research browser --screenshot PATH` is gone. Agents with the read-only retrieval grant used
it to write files, which widened the grant (C-3). Where a screenshot is genuinely needed, call the library from
code that is allowed to write:

```python
from agentteams.research.browser import browser_screenshot
browser_screenshot(url, output_path)
```

#### Step 6 (optional) — Confirm signing end to end

On a signing-governed team, sign two decisions in a row into a scratch copy of the log, then run
`--verify-integrity`. The chain must hold across both rows; under rc.9 it broke at the second.

### Moving to a release checkout (consumers)

From 1.0, consumer renders run agentteams from a **detached, read-only checkout at a signed release tag**. They no
longer run the shared development checkout, which tracks `main`. The development checkout stays on `main` for
development only.

The model:

1. **One checkout per tag.** Clone the repository and check out the tag (`v1.0.0`, later `v1.0.x`) detached, in a
   directory of its own. Do not move that checkout to a newer tag: a new tag gets a new directory.
2. **Verify the tag before using it,** with repository configuration neutralised and `allowed_signers` kept outside
   every agent write root, as `SECURITY.md` "Signing from a trusted install" describes. Check the key
   fingerprint, not only "Good signature". Tags from `v1.0.0-rc.9` onward are signed with RSA
   `SHA256:IPdnLwlF…`.
3. **Install from that checkout into its own environment**, not as an editable install of the development
   checkout. Then make the checkout read-only, and keep it, and the environment, outside every agent write root.
4. **Render from that environment,** so the gate pin (`<hooks>/agentteams-python`) names it. A team rendered earlier
   from the development checkout's interpreter keeps a pin to that interpreter, and its gate keeps importing
   whatever that interpreter imports. That may be the moving `main`. Re-render (step 3), then run `--check-wiring`
   (step 4).
5. **Pin the tag, not a SHA,** in `toolchain.lock` files and fleet sync rounds.
6. **To upgrade to `1.0.x`:** create a new tag checkout, verify it, and install it. Run `--verify-integrity` and the
   HALT review (step 1), then `--migrate-decision-log` (step 2), from the new install **before re-rendering**. Then re-render and run `--check-wiring`.
7. **To roll back:** point back at the previous tag's checkout and environment. Run `--verify-integrity` and the
   HALT review from that install **before re-rendering**, then re-render. Old checkouts cost nothing to keep.

### Rollback A: turning `write_policy` off

Re-rendering without the switch is **not** a clean rollback (remediation log row 344, measured in a pilot rollback
drill). Do the steps below in order. Steps that delete files are destructive and need @security clearance first
(C-5).

1. **Drain the runner.**
    - List pending staged work with `--list-staged`, and settle each item with `--apply-staged SID` or
      `--reject-staged SID`.
    - Run `--verify-proposal-ledger`.
    - Stop the `agentteams --serve-requests` process.
2. **Edit the brief.** Remove `write_policy` (or set it to `"default"`), `write_policy_frameworks` and
   `mcp_grants`. `mcp_grants` requires the switch. `agent_policies` is used only by the proposal and runner path.
3. **Re-render** with `--update --merge`. Then run `--reconcile-front-matter` to see the diverging keys, and
   `--reconcile-front-matter --reconcile-apply` to take the template's values. That restores the agents' tools.
   Restoring `Write`/`Edit` widens grants (C-3), so review each `[CAPABILITY GRANT CHANGED]` line.
4. **Remove what the re-render leaves** (row 344):
    - **R1:** the `mcpServers` block naming `agentteams_runner` in each formerly granted agent's front matter.
      Reconcile never removes a key the template no longer declares. Search the agents directory for
      `agentteams_runner`. A leftover block becomes live again if `--serve-requests` is ever restarted.
    - **R2:** every orphaned `write_policy` fenced section, from `<!-- AGENTTEAMS:BEGIN write_policy v=1 -->` to
      `<!-- AGENTTEAMS:END write_policy -->`. The merge keeps orphaned fenced sections by design, and left in place
      they tell agents they cannot write. Also search the agents and the top-level instructions file (`CLAUDE.md`,
      `AGENTS.md`, `copilot-instructions.md`) for headings beginning `## Write Policy:`.
    - **R3:** `.agentteams/bin/agentteams-runner-mcp.py` and `.agentteams-status/`.
    - **Runner state** (created at run time by the runner and the CLI; row 344 does not list it):
      `.agentteams-queue/`, once it holds no pending work, and `.agentteams/queue-results/`,
      `.agentteams/queue-claimed/`, `.agentteams/runner.lock` and `.agentteams/runner.heartbeat`, once the runner
      is stopped.

    !!! warning "Do not delete `.agentteams/` as a whole"
        It also holds state the team still uses, such as the proposal ledger, template pins and canonical copies.
        Remove only the paths listed above.

5. **Live settings, keys and grants.** agentteams emits only the settings example, and the re-render restores it.
   Anything you merged into the live `.claude/settings.json` for the switch stays until you remove it. That
   includes the `Read(/.agentteams/**)`, `Read(/.agentteams-queue/**)` and `Edit(/.agentteams-status/**)` rules
   and the sandbox `denyWrite` entries.
    - **Keep the sandbox deny on `~/.config/agentteams/keys/`.** Other teams may share that key directory. Remove
      the deny only if you retire the key.
    - **Revoke or expire `direct`-mode grants** signed with `--sign-grant`: remove their records from the operator
      grants file under `~/.config/agentteams/mcp-grants/`, or let them expire.
6. **Verify:** `--verify-integrity`, and `--check-wiring` for the gate interpreter.

The drill covered Claude (and Goose before the switch was refused there). For a Codex team under the switch, also
expect `.agentteams/bin/codex-role-gate.py` and its `.codex/hooks.json` wiring. That path has not been
rollback-drilled.

### Rollback B: reverting to rc.9

Prefer staying on 1.0 and reporting the problem. If you must revert:

1. **Turn `write_policy` off first (Rollback A).** Under rc.9 the switch is unsafe or does not work on Claude:
    - pre-update backups under `.claude/agents/.agentteams-backups/` are loadable again and can shadow narrowed
      agents (row 358, fixed after rc.9);
    - the sandboxed orchestrator cannot reach the runner (row 339, fixed after rc.9);
    - rc.9 does not refuse the switch on Goose.
2. **Review the HALTs with the 1.0 install** while you still have it (step 1). rc.9 has no HALT report, and on
   rc.9 a HALT matched only by token stops blocking (see the table).
3. **Check the decision log** against the "Decision-log chain" row below. Start a fresh log first if rc.9 would
   reject it.
4. **Point consumers back to a verified `v1.0.0-rc.9` checkout** (signed with RSA `SHA256:IPdnLwlF…`). Run
   `--verify-integrity` from that install **before re-rendering**, then re-render from it.

What returns on rc.9, and what to do:

| Area | On rc.9 | Do |
|---|---|---|
| Decision-log chain | A log fully chained by 1.0 verifies on rc.9: the digest and signed fields are identical. rc.9 accepts **no** legacy prefix, so a migrated log that keeps an empty-link legacy prefix of more than one row breaks on rc.9, and that blocks every gated action. Defect 338 also returns for new signatures: the second signed row breaks the chain and the gate refuses every action. | Before rolling back, start a fresh log, or keep the 1.0 install. Avoid signing new decisions on rc.9. |
| HALT matching | Prefix-only again: a HALT recorded under an instance id (`m6-overwrite-…`) does not block `overwrite`. | While on rc.9, record HALTs with the action id as the prefix (`overwrite-…`). |
| Gate interpreter | rc.9's gate ignores the pin. When `python3` cannot import agentteams, the crash is a **silent allow** (cooperative) or deny-all (confined). | Make sure the session's `python3` can import agentteams. |
| Gate decision log | rc.9 ignores `AGENTTEAMS_GATE_LOG`, so the gate's decision record stops **without a warning**. | Do not treat a missing line as a pass. Treat the log as stopped from the rollback onward. |
| Sidecars and backups | rc.9 writes loadable `.lost.<sid>.md` sidecars and loadable backups inside the agents directory. rc.9's `--restore-backup` strips `.agentteams-bak` from any name, so backups taken by 1.0 restore on rc.9. | Move `.agentteams-backups` out of the agents directory after every update. |
| Research | `--screenshot` returns, and the retrieval grant can write files with it again. | Do not rely on the retrieval grant being read-only. |
| Security intel | A corrupt cache reads `fresh` again. | Check the cache before trusting a fresh result. |
| Interop | Projections can widen grants. | Avoid `--interop-from` onto Codex, agents-md or Goose. |
| Memory index | rc.9 rejects a sharded index manifest. Its `--refresh-index` writes one large file and leaves the parts. | Rebuild with rc.9's `--refresh-index`, then delete the `memory-index.part-*.json` files. |
| Grok | rc.9 has no grok adapter. | Grok teams cannot be re-rendered on rc.9. |
| Decision logs migrated or padded by 1.0 | `--migrate-decision-log` does not exist in rc.9. A migrated log that keeps a legacy prefix of more than one row breaks (see "Decision-log chain"). | Start a fresh log before rolling back, or keep the 1.0 install. |

---

## When to Use This Guide

Read this guide if you:

- Have an agent team that was generated before section fencing was introduced and want to bring it under merge-safe management
- Want to use `--update` but your team's files have no fence markers yet
- Need to roll back a migration that produced unexpected results

---

## What Migration Does

Before section fencing existed, AgentTeams generated agent files as flat Markdown with no structure markers. `--update` depends on fence markers to know which regions to replace. Running merge on a pre-fencing file would fail gracefully (no markers to match) but leave the file stale.

The `--migrate` flag performs a one-time legacy migration:

1. Verifies the project is a git repository
2. Creates a `pre-fencing-snapshot` git tag at current `HEAD`
3. Re-runs generation in overwrite mode (`--overwrite --yes`) so agent files are fully regenerated with fenced templates
4. Prints a post-migration checklist for restoring project-specific rules in USER-EDITABLE regions

After migration, `--update` can manage the file normally.

---

## Pre-Migration Checklist

Before running `--migrate`:

- [ ] Working tree is clean (`git status` shows no uncommitted changes)
- [ ] You have run `--dry-run` to review the snapshot-tag + overwrite migration behavior
- [ ] You understand which regions are USER-EDITABLE (they are preserved by future `--update` runs, but `--migrate` itself performs full overwrite regeneration)
- [ ] You have a backup or recent commit to recover from if needed

---

## Running a Migration

### Step 1 — Dry Run First

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --migrate --dry-run
```

Review the output for:

```
Snapshot tag 'pre-fencing-snapshot' created at HEAD.
Running --overwrite migration...
```

Note: `--migrate --dry-run` still creates the `pre-fencing-snapshot` tag before delegating. If you were only previewing, delete the tag manually (`git tag -d pre-fencing-snapshot`) before a real migrate run.

### Step 2 — Run Migration

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --migrate
```

`--migrate` is full regeneration, not selective insertion. Existing generated files are overwritten with current fenced template output.

Backup files are created before modification.

### Step 3 — Review and Commit

```bash
git diff .github/agents/
git add .github/agents/
git commit -m "chore: migrate agent team to fence-compatible format"
```

The `pre-fencing-snapshot` tag is created automatically by `--migrate` and is used for rollback.

---

## After Migration

Once migration is complete, use `--update` for all subsequent updates. You do not need to run `--migrate` again for already-migrated files.

If you add new templates to the team later, any new files written by a post-fencing generation are fence-compatible from creation. Only files generated before fencing existed need migration.

---

## Rolling Back: `--revert-migration`

If migration produced unexpected results — for example, section content was inserted in the wrong position or template content conflicts with existing prose — roll back:

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --revert-migration
```

`--revert-migration`:

1. Runs `git reset --hard pre-fencing-snapshot` in the project repository
2. Deletes the `pre-fencing-snapshot` tag
3. Leaves the working tree at the pre-migration commit state

Equivalent manual fallback:

```bash
git reset --hard pre-fencing-snapshot
git tag -d pre-fencing-snapshot
```

---

## Edge Cases

### Mixed team: some files pre-fencing, some post-fencing

`--migrate` still performs overwrite regeneration. If your team is mixed, prefer a dry run and diff review before applying.

### Dirty working tree

`--migrate` warns when uncommitted changes exist, but it does not block execution. Commit first if you need deterministic rollback and clean migration diffs.

### Template updated between generation and migration

If the template has been updated since your files were generated, `--migrate` inserts content from the **current** template version. After migration, run `--update` immediately to confirm the remaining non-fenced regions are still aligned.

---

## CLI Reference Summary

| Flag | Purpose |
|---|---|
| `--migrate` | Add fence markers and initial template content to pre-fencing files |
| `--revert-migration` | Restore files by `git reset --hard pre-fencing-snapshot` and then delete that tag |
| `--dry-run` | Preview migration changes without writing |
| `--no-backup` | Skip backup creation during migration (not recommended) |

---

## Best Practices

- **Always commit before migrating.** The pre-fencing state should be preserved in git history, not just on disk.
- **Do not pre-create `pre-fencing-snapshot`.** `--migrate` creates that tag automatically and aborts if it already exists.
- **Migrate and merge in the same sitting.** Migrating and then leaving files in limbo (without running a subsequent `--update`) creates a window where the file has markers but may have stale content inside them.
- **Review each MIGRATE/SKIP line** in the dry run output. A section marked SKIP (USER-EDITABLE) will not receive template content — confirm that's correct for each skip.
