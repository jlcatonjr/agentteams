# Part VI — OS confinement

## A checklist for the software you ship {#S17}

There is a curated eight-layer checklist for securing the software a project
actually ships — covering governance, identity, secrets, hardening the host,
network, the application and its supply chain, monitoring, and backup/recovery.
It is careful to draw a line: this checklist is about the *shipped system*, while
the guard is about the *build*; the two are neighbors and must not be blurred. The
honest note: it is guidance only. A checklist informs, but it secures nothing by
itself, and the tools it names are not installed for you.

## The locked room, and why it starts unlocked {#S18}

There are three settings for how tightly a helper is confined: cooperative (no
locked room — today's default), confined, and exclusive (which also blocks reading
certain sensitive things). An unrecognized setting fails safe rather than quietly
loosening. Two plain cautions belong here. First, **by default the strongest
locks are off** — the locked room starts switched off, and agentteams only writes
the *blueprint* for the room; it does not build it. The real enforcement is the AI
tool's own operating-system sandbox, and the blueprint does nothing until someone
wires it in. Second, **this is proven on Linux; on Macs it is advice, not a proven lock** (it has not
been tested there), and on Windows there is no built enforcement at all. On a system that can't enforce it, the tool
refuses to pretend it can (it fails safe) unless you explicitly allow the
unenforced mode. It also marks certain control files as never-writable, so a
confined helper can't reach out and disable its own guardrails.

## A checkpoint before every move {#S19}

A checkpoint runs before each action a helper takes — catching moves that the
front-door locks never see. It flatly denies clearly-dangerous writes (secrets,
personal information, trick-text) and asks a human first before risky commands
(installing software, piping downloaded content into a shell, force-deleting, and
the like). Before it inspects anything, it first checks that the inspector itself
hasn't been tampered with, using the tamper-evident seal; if it has, the
checkpoint asks rather than silently allowing. One plain caution: **by default
this checkpoint is permissive** — it only becomes strict once you turn on the
locked-room settings. And the honest ceiling: it does not stop a determined
insider who edits several files at once — it raises the cost from one quiet edit
to three, each of them visible in the project's version history.
