# Part VIII — Integrity, provenance, and recovery

## The tamper-evident seal {#S22}

The system keeps a set of fingerprints over the files that do the safety work,
stored together in one place — and that fingerprint list includes itself, so even
removing an entry shows up. Checking the seal reports anything that was modified,
went missing, or isn't listed. A *missing* seal simply means "never set up," which
is deliberately not treated as "tampered with," and rebuilding the seal is always
a deliberate human act. The honest ceiling, plainly: a seal sitting right next to
the files it protects is a speed bump, not a wall — whoever can edit a protected
file can also edit the seal. What it does buy is real: it makes both accidental
drift and deliberate meddling obvious and costly — a recorded, multi-step change
that shows up in the project's history — but it does not make tampering
impossible.

## A label saying how something was made {#S23}

There is an optional label that records how a given artifact was produced — who or
what made it, when, fingerprints of the inputs, and a required list of its known
limitations. It never quietly fills that limitations list with something
reassuring; an empty list has to be a deliberate, stated "none declared." It is a
reusable tool that things can choose to carry, not a stamp automatically applied
to everything.

## Photocopies in a safe, and a reference snapshot {#S24}

Before any destructive write, the system quietly makes a dated backup copy of the
files, each with its own fingerprint and a note on why the backup was made. You
can list the backups, restore one, verify one against its recorded fingerprint,
prune old ones (the newest is always kept), and optionally copy them off the
machine. Separately, a "baseline" is a fingerprint snapshot of a freshly built
project that can be compared exactly later on, so unexpected drift gets caught.
