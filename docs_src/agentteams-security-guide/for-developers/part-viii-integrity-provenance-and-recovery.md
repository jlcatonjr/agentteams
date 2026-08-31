# Part VIII — Integrity, provenance, and recovery

## Integrity manifests  ✅ {#S22}

The controls this project relies on are Python files in the same tree the agents
can edit — the gate, scanner, fence engine, capability-merge, red-team checks,
sandbox emitters, the hook. An agent that can write `security_gate.py` doesn't
need to defeat the gate. The integrity manifest is the response.

**What it is.** A **SHA-256 manifest over the enforcement modules**, stored at
`references/enforcement-integrity.json`. The tuple **includes `integrity.py`
itself**, so removing an entry — which would silently drop a module from coverage
— is itself detectable (`agentteams/integrity.py:1-188`, list at
`ENFORCEMENT_MODULES`).

**The commands:**

```
agentteams --write-integrity-manifest    # regenerate — an explicit operator act, never auto-run
agentteams --verify-integrity            # report findings; empty output means everything matches
```

`verify()` reports three finding reasons (`agentteams/integrity.py:141-178`):

| Reason | Meaning |
|---|---|
| **`modified`** | a tracked module's bytes no longer match the recorded digest |
| **`missing`** | a tracked module is absent |
| **`unmanifested`** | a module in `ENFORCEMENT_MODULES` the manifest doesn't record — reported deliberately, because **under-coverage is the manifest's own failure mode** |

**A missing manifest returns `[]`** (`integrity.py:153-155`) — "never set up" is
deliberately not "compromised," so the check doesn't fire on every fresh clone
until someone mutes it. Callers that *require* a manifest must check for the file
themselves. Regeneration is an **explicit operator act, never auto-run**: an
auto-refreshed manifest verifies nothing, because an attacker's edit would be
re-blessed on the next build (`agentteams/cli/commands.py:170-266`).

**Honest ceiling — a speed bump, not a boundary.** Stated in-module: a hash
manifest beside the files it protects is a **speed bump, not a boundary**. One who
can edit `scan.py` can edit `enforcement-integrity.json`; one who can edit
`integrity.py` can make `verify()` return `[]`. What it *buys*: detects accidental
drift (most real damage); makes tampering a **multi-step, recorded** act visible
in `git diff`; and gives the S19 hook something concrete to check before trusting
the scanner. The durable fix is elsewhere — move enforcement into the harness and
keep signing keys outside the agent's environment.

**Source.** `agentteams/integrity.py:1-188`; `agentteams/cli/commands.py:170-266`.

## Provenance stamps  ⚙ *(library / pattern, not auto-wired into the default emit path)* {#S23}

`Provenance` is a small reusable stamp of **how a generated artifact was
produced** (`agentteams/provenance.py:1-99`): the **generator**, a
**`generated_at`** timestamp, **input SHA-256 prefixes** (`{label: prefix}`), and
a **required `provisional` list** of known limitations.

- **`generated_at` is passed in, never read from the clock** — a reproducibility
  constraint (an implicit clock read would break resume/determinism).
- **Honest-by-construction:** `provisional` is a required, explicit field; an
  **empty list is a deliberate assertion**, rendered `Provisional: none declared
  (deliberate)` (`provenance.py:86-90`), so it can never be confused with an unset
  one.

**Honest ceiling (⚙ marker).** It is a **library, not an active pipeline stage** —
stdlib-only, reusable for manifests/indices/eval outputs/fleet reports, **not
auto-emitted into the default generation pipeline**. Reading this as "every
generated artifact already carries a provenance stamp" is the overclaim the marker
prevents: wiring it in is a caller's deliberate choice. A stamp records what its
caller told it; it does not independently verify the declared inputs, and an
artifact that never calls the library carries no stamp.

**Source.** `agentteams/provenance.py:1-99`.

## Backups and baselines  ✅ {#S24}

Two mechanisms: **backups** (pre-write snapshots for rollback) and **baselines**
(a fingerprint of a generated tree for detecting emission drift).

**Backup-before-destructive.** Before any destructive write — unless
`--no-backup`/`--dry-run` — agentteams snapshots to
`<output>/.agentteams-backups/YYYYMMDD-HHMMSS/`, with a per-file `_manifest.json`
recording full SHA-256 plus reason/framework/version (`agentteams/backup.py:1-497`).
The SHA is **hashed from the backup copy**, not the source
(`agentteams/backup.py:107-109`) — closing a TOCTOU window, so the digest
describes exactly the bytes saved.

**The backup verbs:**

```
agentteams --list-backups
agentteams --restore-backup LABEL|latest    # snapshot-complete rollback — itself gated by the destructive gate (S11)
agentteams --verify-backup                  # re-hash each file vs recorded SHA → PASS / FAIL / MISSING
agentteams --prune-backups [KEEP]           # union/fail-safe retention — the single newest is ALWAYS kept
agentteams --backup-mirror <dir>            # best-effort off-machine copy; non-fatal on failure
```

Prune uses a union rule (`agentteams/backup.py:387-401`): a snapshot is retained
if within the `keep_last` window **or** (when `keep_within_days` is set) inside
it; the always-newest guard (`idx == 0`) means the single most recent is retained
**even at `KEEP 0`** — prune cannot leave zero snapshots. `--verify-backup`
reports MISSING for a backup with no `_manifest.json`. The off-machine mirror is
also set via `AGENTTEAMS_BACKUP_MIRROR`; a mirror failure is non-fatal and does
not block the primary backup (`agentteams/cli/backup_switch.py:1-85`).

**Baselines detect emission drift.**

```
agentteams --capture-baseline    # SHA-256 manifest of a generated tree
agentteams --check-baseline      # compare byte-for-byte; EXITS 2 on drift
```

The manifest **hashes raw file bytes only** — timestamps, mtime, filesystem
ordering excluded (`agentteams/baseline.py:15`) — so baselines are **stable across
machines** and a drift finding reflects a real content change
(`agentteams/baseline.py:1-131`; `agentteams/cli/app.py:133-163`).

**Honest ceiling.** A backup makes damage *reversible*; a baseline makes drift
*detectable* — neither *prevents* the write or the drift. Backups share the
tree's own trust boundary (an attacker who can write the output can write
`.agentteams-backups/`), the mirror is best-effort not guaranteed, and a baseline
tells you the emission changed — not whether the change was intended.

**Source.** `agentteams/backup.py:1-497`; `agentteams/baseline.py:1-131`;
`agentteams/cli/backup_switch.py:1-85`; `agentteams/cli/app.py:133-163`.

---

**Sources for Part VIII.** `agentteams/integrity.py`;
`agentteams/cli/commands.py`; `agentteams/provenance.py`;
`agentteams/backup.py`; `agentteams/baseline.py`;
`agentteams/cli/backup_switch.py`; `agentteams/cli/app.py`.
