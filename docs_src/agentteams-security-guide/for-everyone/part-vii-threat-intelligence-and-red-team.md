# Part VII — Threat intelligence and red team

## The daily safety bulletin {#S20}

When the tool is set up and each time it updates, it pulls current security news
from a handful of trusted public sources — plus a fixed top-ten list of common AI
risks — always over a strict allowlist of approved sources. It uses this to fill
in the "current threats" sections and stamps them with the moment they were
gathered: valid as of that time, not a permanent truth. If the news is more than a
day old or can't be fetched, it is marked stale, a warning is added, and the
stale-news lock kicks in.

## Practicing break-ins to find weak spots {#S21}

The team runs a repeating drill in which it deliberately attacks its own defenses,
reviews what happened, plans fixes, and — the part that sets it apart from an
ordinary test — also audits the audit itself, repeating until two clean rounds in
a row turn up nothing new. Each attack is graded against defined attacker levels
and clear outcomes: defended, partly held, broke through, a known accepted limit,
or out of scope; an outcome nobody recognizes is treated as a bug, not a pass. The
attacks are run on an isolated copy, and afterward the real files are confirmed
untouched. The drill also watches for the six classic ways a self-test can fool
itself — like a checker rigged to always pass, or a coverage claim resting on a
denominator no one examined.
