# Part VIII — Integrity, provenance, and recovery

## Integrity manifests  ✅ {#S22}

**The adversary this addresses is the sharpest one in the system.** The controls
this project relies on are themselves Python files sitting in the same working
tree the agents can edit — the destructive gate, the scanner, the fence-restore
engine, the capability merge, the red-team checks, the sandbox emitters, the
hook. A red-team probe (E4) recorded the umbrella finding plainly: *the
constitution's referee plays inside the same sandbox as the players.* An agent
that can write the gate does not need to defeat it. The integrity manifest is the
response to that class of tampering.

**What it is.** A **SHA-256 manifest over the enforcement modules**, stored at
`references/enforcement-integrity.json`. Crucially, the tracked set **includes
`integrity.py` itself**, so removing a module's entry — which would silently drop
it from coverage — is itself detectable. `verify()` reports **`modified`**,
**`missing`**, or **`unmanifested`** (a tracked module the manifest does not
record — reported deliberately, because **under-coverage is the manifest's own
failure mode**).

**A missing manifest returns `[]` — "never set up" ≠ "compromised."** An
unmanifested repository is not a tampered one; firing on every fresh clone would
train operators to mute the check, and a muted check protects nothing.
Regenerating the manifest (`--write-integrity-manifest`) is an **explicit operator
act, never auto-run** — an auto-refreshed manifest would re-bless an attacker's
edit on the next build and verify nothing.

**Honest ceiling — a speed bump, not a boundary.** Stated in-module and central
to a reviewer's model: a hash manifest stored beside the files it protects is a
**speed bump, not a boundary.** One who can edit the scanner can edit the
manifest; one who can edit `integrity.py` can make `verify()` return `[]`. What it
*buys*, honestly: it detects **accidental** corruption and drift (most real-world
damage); it makes tampering a **multi-step, recorded** act visible in `git diff`
(the E4 cost-raising of S19/S25); and it gives the PreToolUse hook something
concrete to check before trusting the scanner. The durable fix is elsewhere: move
enforcement into the harness, and keep signing keys outside the agent's
environment. Full module treatment: Edition R, S22.

**Source.** `agentteams/integrity.py:1-188`;
`agentteams/cli/commands.py:170-266`.

## Provenance stamps  ⚙ *(library / pattern, not auto-wired into the default emit path)* {#S23}

**The concern:** a downstream reader mistaking a provisional snapshot for a
settled result. `Provenance` is a small, reusable stamp recording **how a
generated artifact was produced** — the generator, a `generated_at` timestamp,
input SHA-256 prefixes, and a **required `provisional` list** of known
limitations.

Two design choices matter to a reviewer. First, **`generated_at` is passed in,
never read from the clock** — a reproducibility constraint (an implicit clock read
would break resume and determinism). Second, it is **honest-by-construction:**
`provisional` is a required, explicit field, never defaulted to a reassuring
value, and an **empty list is a deliberate assertion** rendered
`Provisional: none declared (deliberate)` — so an empty caveat list can never be
confused with an unset one.

**Honest ceiling — and note the ⚙ marker.** Provenance is a **library, not an
active pipeline stage:** it is stdlib-only and reusable, but **not auto-emitted
into the default generation pipeline.** Reading this as "every generated artifact
already carries a provenance stamp" is the overclaim the marker exists to prevent.
And a stamp records what its *caller* declared — it vouches those declarations are
recorded honestly; it does not independently verify the declared inputs were the
ones used, and an artifact that never calls the library carries no stamp at all.
Full treatment: Edition R, S23.

**Source.** `agentteams/provenance.py:1-99`.

## Backups and baselines  ✅ {#S24}

The recovery layer accepts that damage and drift can happen and makes them
**reversible** and **detectable** respectively — it does not pretend to prevent
them.

**Backup-before-destructive.** Before any destructive write (unless `--no-backup`
or `--dry-run`), agentteams snapshots the output tree to
`<output>/.agentteams-backups/YYYYMMDD-HHMMSS/` with a per-file `_manifest.json`
of full SHA-256 plus reason/framework/version. The SHA is **hashed from the backup
copy, not the source** — deliberately closing a time-of-check/time-of-use window,
so the recorded digest describes exactly the bytes saved. Verbs: `--list-backups`;
`--restore-backup LABEL|latest` (snapshot-complete rollback, **itself gated by the
destructive gate**, S11); `--verify-backup` (re-hash vs recorded SHA →
PASS/FAIL/MISSING); `--prune-backups [KEEP]` (union/fail-safe retention — **the
single newest is always kept, even at KEEP 0**); `--backup-mirror` (best-effort
off-machine copy, **non-fatal on failure**).

**Baselines detect emission drift.** A baseline (`--capture-baseline` /
`--check-baseline`) is a SHA-256 manifest of a generated tree, compared
byte-for-byte to detect **emission drift** — an unintended change in what the
generator emits. `--check-baseline` **exits 2 on drift.** It hashes **raw file
bytes only** (timestamps, mtime, ordering excluded), so a baseline is **stable
across machines** and a drift finding reflects a real content change, not an
environmental difference.

**Honest ceiling.** A backup makes damage *reversible* and a baseline makes drift
*detectable*; neither *prevents* the write or the drift. Backups share the tree's
own trust boundary — an attacker who can write the output can write
`.agentteams-backups/` — the off-machine mirror is best-effort, and a baseline
tells you the emission changed, not whether the change was intended. Full verb and
line-number detail: Edition R, S24.

**Source.** `agentteams/backup.py:1-497`; `agentteams/baseline.py:1-131`;
`agentteams/cli/backup_switch.py:1-85`; `agentteams/cli/app.py:133-163`.

---

**Sources for Part VIII.** `agentteams/integrity.py`;
`agentteams/cli/commands.py`; `agentteams/provenance.py`; `agentteams/backup.py`;
`agentteams/baseline.py`; `agentteams/cli/backup_switch.py`;
`agentteams/cli/app.py`. Line-precise provenance: `SOURCES.md` (S27).
