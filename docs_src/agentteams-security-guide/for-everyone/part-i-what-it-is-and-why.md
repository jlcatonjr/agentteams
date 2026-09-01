# Part I — What it is and why

## What this safety system is (and isn't) {#S1}

agentteams builds a coordinated team of AI helpers for a software project from a
single description. It comes with a safety system for one plain reason: an AI
helper follows instructions and can edit files, run commands, and reach into
other projects — so it could be steered, either by sneaky text hidden in
something it reads or by its own honest mistake, into doing real damage:
deleting things, making sweeping changes, touching other projects, or brushing
up against passwords and keys. The realistic troublemaker here isn't an outside
hacker breaking in; it's a trusted helper with permission to write, tricked into
misusing it. So the system wraps the helpers in overlapping layers of safety:
think of a rule book everyone follows, a guard who can shout "stop," signed
permission slips, locked doors, an inspector reading the mail, a locked room, a
daily safety bulletin, practice fire drills, and tamper-evident seals with
backups in a safe. No single layer is "the" protection — they back each other
up, and later parts show how they compose. Two plain-spoken cautions belong
right here. **None of this runs inside the app you eventually ship** — the guard
works while your software is being *built*, not inside the finished product you
hand to customers; an app that shows AI answers to real users needs its own,
separate safety on top of this. And **by default the strongest locks are
switched off** — the everyday rules and the guard are always on, but the
toughest locks (the ones that fence a helper into a locked room) start dormant
and only come alive when you deliberately turn them on.

## Two different jobs, and where the safety lives {#S2}

There are two jobs people must not blur together: keeping the *team that builds*
the software safe, and keeping the *software you ship* safe. This system does the
first, and only *reviews your plans* against good practice for the second. The
safety itself lives in three places, kept deliberately separate: automatic
locked doors on the main entrances; a checkpoint that watches the helpers' own
moves (which the front doors never see); and the guard's own judgment as it reads
and rules. The honest version, told plainly: some of these are true automatic
locks and some are a person's judgment — never mistake advice for a lock. A note
left next to the files it protects is a speed bump, not a wall; a signed slip
stops a stranger, not someone holding the signing pen; a locked room does nothing
until someone actually turns the key to wire it in; and the locked-room feature
is proven on Linux (on Macs it is advice, not a proven lock; on Windows there is no
lock). These limits are facts, and they travel with every part of this guide.
