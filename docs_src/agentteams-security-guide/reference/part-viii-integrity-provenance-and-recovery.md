# Part VIII — Integrity, provenance, and recovery

## Integrity manifests  ✅ {#S22}

The controls this project relies on are themselves Python files sitting in the
same working tree the agents can edit — the destructive-action gate, the content
scanner, the fence-restore engine, the capability merge, the red-team checks,
the sandbox emitters, the hook. A red-team audit (probe E4) recorded the
umbrella finding plainly: *the constitution's referee plays inside the same
sandbox as the players*. An agent that can write `security_gate.py` does not
need to defeat the gate. The integrity manifest is the response to that class of
tampering.

**What it is.** A **SHA-256 manifest over the enforcement modules** — the Python
files that implement C-1..C-5 — stored at
`references/enforcement-integrity.json`. The tracked set includes the gate, the
decision log, the scanner, the fence engine, the capability-merge / front-matter
reconciliation, the phase-6 red-team checks and registry, the privilege/grant
emitters, and the sandbox emitter (`agentteams/integrity.py:1-188`, list at
`ENFORCEMENT_MODULES`). Crucially, the tuple **includes `integrity.py` itself**,
so removing a module's entry — which would silently drop it from coverage — is
itself detectable rather than invisible.

**What `verify()` reports.** `verify()` returns a finding per mismatch and an
empty list when everything matches (`agentteams/integrity.py:141-178`). The
three finding reasons are:

- **`modified`** — a tracked module's bytes no longer match the recorded digest;
- **`missing`** — a tracked module is absent;
- **`unmanifested`** — a module in `ENFORCEMENT_MODULES` that the manifest does
  not record. This is reported deliberately, because **under-coverage is the
  manifest's own failure mode**: a module added to the tracked set but never
  written into the manifest is unverified, and silence there would be the gap.

**A missing manifest returns `[]` — "never set up" ≠ "compromised."** When the
manifest file does not exist, `verify()` returns `[]` (`integrity.py:153-155`).
An unmanifested repository is not a tampered one; treating "never set up" as
"compromised" would make the check fire on every fresh clone until someone muted
it — and a muted check protects nothing. Callers that *require* a manifest must
check for the file themselves. Regenerating the manifest
(`--write-integrity-manifest`, `agentteams/cli/commands.py:170-266`) is an
**explicit operator act**, never auto-run: an auto-refreshed manifest verifies
nothing, because the attacker's edit would be re-blessed on the next build.

**Honest ceiling — a speed bump, not a boundary.** Stated in-module: a hash
manifest stored beside the files it protects is a **speed bump, not a boundary**.
One who can edit `scan.py` can edit `enforcement-integrity.json`; one who can
edit `integrity.py` can make `verify()` return `[]`. What it *does* buy, honestly
enumerated: it detects **accidental** corruption and unnoticed drift (most
real-world damage); it makes tampering a **multi-step, recorded** act — the
attacker must also regenerate the manifest, and that regeneration shows up in
`git diff`; and it gives the S19 PreToolUse hook something concrete to check
before it trusts the scanner it is about to call. The durable fix is elsewhere:
move enforcement into the harness, where agents cannot write it, and keep signing
keys outside the agent's environment.

**Source.** `agentteams/integrity.py:1-188`;
`agentteams/cli/commands.py:170-266`.

## Provenance stamps  ⚙ *(library / pattern, not auto-wired into the default emit path)* {#S23}

`Provenance` is a small, reusable stamp recording **how a generated artifact was
produced** (`agentteams/provenance.py:1-99`). It carries the **generator** (the
module/script that produced the artifact), a **`generated_at`** timestamp, a set
of **input SHA-256 prefixes** (`{label: sha256-prefix}`), and a **required
`provisional` list** of known limitations. The design intent is that no reader
downstream ever mistakes a provisional snapshot for a settled result.

**`generated_at` is passed in, never read from the clock.** The caller supplies
the timestamp explicitly; the module never calls `datetime.now()` itself. This
is a reproducibility constraint — provenance is used in contexts (workflow
scripts, resumable jobs) where an implicit clock read would break resume and
determinism.

**Honest-by-construction.** `provisional` is a **required, explicit field**,
never defaulted to a reassuring value. An **empty list is a deliberate
assertion** that nothing is known-provisional — and it renders that way, as
`Provisional: none declared (deliberate)` (`provenance.py:86-90`), so an empty
caveat list can never be confused with an unset one. The stamp cannot silently
imply "no known limitations" the way a defaulted field would.

**It is a library, not an active pipeline stage.** Provenance is a reusable,
stdlib-only building block for manifests, indices, eval outputs, and fleet
reports to carry the same stamp. It is **not auto-emitted into the default
generation pipeline** — hence the ⚙ marker. Reading this section as "every
generated artifact already carries a provenance stamp" is the overclaim the
marker exists to prevent: the *pattern* exists and is honest-by-construction, but
wiring it into a given artifact is a caller's deliberate choice, not a default of
the emit path.

**Honest ceiling.** A provenance stamp records what its caller told it — the
generator, the inputs it was handed, the caveats it was given. It vouches that
those declarations are recorded honestly (nothing is defaulted to reassure); it
does not independently verify that the declared inputs are the ones actually
used, and an artifact that never calls into the library carries no stamp at all.

**Source.** `agentteams/provenance.py:1-99`.

## Backups and baselines  ✅ {#S24}

The recovery layer makes damage reversible and emission drift detectable. Two
distinct mechanisms: **backups** (snapshots taken before a destructive write, for
rollback) and **baselines** (a fingerprint of a generated tree, for detecting
that the generator's output changed).

**Backup-before-destructive.** Before any destructive write — unless
`--no-backup` or `--dry-run` — agentteams snapshots the output tree to
`<output>/.agentteams-backups/YYYYMMDD-HHMMSS/`, with a per-file `_manifest.json`
recording each file's full SHA-256 plus the reason, framework, and version
(`agentteams/backup.py:1-497`).

**The SHA is hashed from the backup copy (TOCTOU).** The per-file SHA-256 is
computed from the **backup copy**, not from the source file
(`agentteams/backup.py:107-109`). This deliberately closes a
time-of-check/time-of-use window: hashing the source separately from copying it
would leave a gap in which the source could mutate between copy and hash, so the
recorded digest describes exactly the bytes that were actually saved.

**The backup CLI verbs.**

| Verb | What it does |
|---|---|
| `--list-backups` | list available snapshots |
| `--restore-backup LABEL\|latest` | snapshot-complete rollback — **itself gated by the destructive gate** (S11) |
| `--verify-backup` | re-hash each file vs the recorded SHA → PASS / FAIL / MISSING |
| `--prune-backups [KEEP]` | union / fail-safe retention — **the single newest is always kept** |
| `--backup-mirror` / `AGENTTEAMS_BACKUP_MIRROR` | best-effort off-machine copy; **non-fatal on failure** |

**Prune keeps the newest even at KEEP 0.** Pruning uses a union rule
(`agentteams/backup.py:387-401`): a snapshot is retained if it is within the
`keep_last` newest window **or** (when a `keep_within_days` window is set) its
timestamp falls inside it. The always-newest guard (`idx == 0`) means the single
most recent snapshot is retained **even at `KEEP 0`** — prune is fail-safe by
construction and cannot leave zero snapshots. `--verify-backup` reports MISSING
for a backup with no `_manifest.json` (an older backup that cannot be verified),
and the mirror is best-effort: a mirror failure is non-fatal and does not block
the primary backup (`agentteams/cli/backup_switch.py:1-85`).

**Baselines detect emission drift.** A **baseline**
(`--capture-baseline` / `--check-baseline`) is a SHA-256 manifest of a generated
tree, compared **byte-for-byte** across runs to detect **emission drift** — an
unintended change in what the generator emits (`agentteams/baseline.py:1-131`).
`--check-baseline` **exits 2 on drift** (`agentteams/cli/app.py:133-163`). The
manifest **hashes raw file bytes only** — timestamps, mtime, and filesystem
ordering are excluded (`baseline.py:15`) — so a baseline is **stable across
machines** and a drift finding reflects a real content change, not an incidental
environmental difference.

**Honest ceiling.** A backup makes damage *reversible* and a baseline makes drift
*detectable*; neither *prevents* the destructive write or the drift. Backups
share the tree's own trust boundary (an attacker who can write the output can
write `.agentteams-backups/`), the off-machine mirror is best-effort rather than
guaranteed, and a baseline tells you the emission changed — not whether the change
was intended.

**Source.** `agentteams/backup.py:1-497`; `agentteams/baseline.py:1-131`;
`agentteams/cli/backup_switch.py:1-85`; `agentteams/cli/app.py:133-163`.

---

**Sources for Part VIII.** `agentteams/integrity.py`;
`agentteams/cli/commands.py`; `agentteams/provenance.py`;
`agentteams/backup.py`; `agentteams/baseline.py`;
`agentteams/cli/backup_switch.py`; `agentteams/cli/app.py`.
