# Part IX — Synthesis and reference matter

## How the layers work together {#S25}

Put together, the layers form one composed defense rather than a single wall. The
rule book sets the principles; the guard and the signed permission slips gate the
*decisions*; the locked doors block *dangerous execution*; the inspector blocks
*bad content*; the locked room bounds *where a helper can reach*; the safety
bulletin and the practice drills keep everything *current and tested*; and the
tamper-evident seals and backups make meddling *obvious* and damage
*recoverable*. Each layer has an honest ceiling, and they are arranged to cover
one another's gaps — the guard's judgment is backed by the inspector, the
inspector by the seal, the seal by the checkpoint, and the checkpoint by the
locked room. The one gap that remains — someone editing the inspector, the seal,
and the checkpoint all at once — is made costly and visible in the project's
history, not eliminated. And two plain reminders carry through to the end: **none
of this runs inside the app you ship** — a delivered app needs its own separate
runtime protection on top — and the locked room is proven only on Macs.

## Plain-language dictionary {#S26}

- **Rule book (the constitution).** The five bedrock rules that outrank everything
  and cannot be rewritten.
- **The guard (the security sentinel).** The look-only helper that reviews risky
  actions and can say "stop."
- **Whose-instructions-win vs who-to-believe.** Two separate rankings: one for
  which instructions to follow, one for which sources to trust about facts; being
  trusted about facts grants no permission to act.
- **Stop.** The guard's final verdict; the work halts and no permission slip gets
  past it.
- **The three slips.** A *clearance* okays a destructive step beforehand; a
  *waiver* lifts a stop at one specific door; a *grant* widens where a helper may
  write into another project.
- **Pass-with-conditions.** Allowed only once every listed condition is confirmed
  done; until then it blocks like a stop.
- **Locked doors (the gates).** Automatic blocks on dangerous or stale-news
  actions at the main entrances.
- **Protecting hand-added notes (shrink-policy).** Keeps a rebuild from silently
  shrinking content you enriched by hand.
- **Safe vs dangerous neighbor-update.** *Merge* touches only marked regions and
  is the safe default; the *overwrite* mode replaces whole files and is dangerous.
- **Marked regions.** Sections a rebuild manages; for safety-critical ones the
  official version always wins.
- **The inspector (the scanner).** The automatic reader that flags secrets,
  personal info, and trick-text; a serious find means stop — though it can still
  be fooled by clever disguises.
- **Locked-room settings.** Cooperative (off by default), confined, or exclusive;
  the stronger settings must be turned on deliberately.
- **The checkpoint.** A check run before each helper action, permissive by default
  and strict only under the locked-room settings.
- **Tamper-evident seal (the integrity check).** Fingerprints that make meddling
  obvious and costly, not impossible.
- **How-it-was-made label (provenance).** An optional record of how an artifact
  was produced, including a required list of known limitations.
- **Backups and baseline.** Dated safe copies for recovery, and a reference
  snapshot to catch unexpected drift.
- **Fire-drill grades.** The outcomes a practice attack is scored with (defended,
  partly held, broke through, known limit, out of scope).
- **Honest ceiling.** The plain statement, kept with every control, of what it
  buys and what it cannot.
