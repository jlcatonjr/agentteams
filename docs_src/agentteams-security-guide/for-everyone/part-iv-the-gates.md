# Part IV — The gates

## The locked door on dangerous actions {#S11}

The main safety door guards actions that overwrite or restore files. When it
fires it announces which door it is, then blocks the action and stops. It will
not let a destructive write through unless a matching, valid permission slip
already exists — recorded beforehand, never after. In plain terms, there's an
easy way to avoid needing a slip at all: choose to *merge* changes rather than
*overwrite* them. One special migration path is exempt, but only through an
explicit switch flipped in the moment — never a hidden global setting — and even
that path brings its own undo.

## The lock that refuses stale safety news {#S12}

A second lock refuses to run a whole build when the security news it relies on
has gone stale — too old (more than a day), stamped with a bad date, or marked
stale. It also checks a fingerprint of that news, so you can't just relabel old
news with today's date to sneak it past; you'd have to actually go fetch fresh
news. For genuinely offline situations it can be cleared with a signed slip, and
when it refuses it tells you how much of the build depends on that news.

## Protecting the notes you added by hand {#S13}

When you enrich a helper's file by hand inside its marked regions, this control
keeps a rebuild from silently shrinking or erasing your additions — it notices
when a section suddenly gets much smaller or loses its specifics. By default it
keeps your version and posts a notice; other settings will warn while saving a
recovery copy, refuse the write outright, or allow it silently. Security-critical
regions are treated specially: there the official version always wins, so an
attacker can't quietly pin a weakened safety section in place, and renaming a
safety region is refused outright.

## Two ways to update a neighboring project — one is dangerous {#S14}

There are two ways to update a neighboring project's entry files. One mode
completely overwrites them; it exists, but it once silently replaced someone's
work, recoverable only because the files were backed up in version control. The
safe default is the *merge* mode, which touches only specially-marked regions and
skips files that hold your own content. Before anyone uses the overwrite mode, a
short four-point pre-flight checklist must all pass. And by design this bridge
carries no locks or permissions across — a neighboring project that needs a
locked room has to set that up for itself.
