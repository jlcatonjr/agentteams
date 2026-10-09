# Part V — Turning the key

<!-- skeleton:SB14 SB15 SB16 SB17 SB24 -->

> **Plain-language ceiling #2 — the lock does nothing until someone turns the key.** The system *hands
> you* a lock; it never installs it into your live setup for you. On Linux, "turning the key" means
> running the worker **through the doorkeeper**. A lock left on the bench locks nothing.

There's a quick way to check the key actually turned — a read-only inspection that reports "yes, it's
wired" or "no, it isn't," without ever reading your secrets.

And the **guard by the door**: for a handful of obviously dangerous actions (deleting things), the guard
stops and asks you first. Be clear-eyed about the guard, though — he watches a *specific list* of dangers
by the main door; he is a helpful speed-bump, **not a wall**. Plenty of side doors he doesn't watch. When
you've *explicitly* asked for the locked room, the guard is set to "if in doubt, stop"; by default he
stays set to "if in doubt, allow" — even in the now-default locked room, unless you asked for it on
purpose — so a jumpy guard never halts honest work. The guard now also stops and asks before anyone **signs off a
finished job for good** (merging a pull request), however the request is sent — but, as before, a
request written in disguise or torn into pieces can still slip past him.

## Only the foreman holds the pen (an optional extra)

You can also ask for a stricter workshop: **only the foreman may order a change**, and even he hands the order to a clerk. Every other worker
can look, but must hand in a written request instead of doing the work. Some workers may be given a
request slot through a **hatch to the back office**. In the back office, outside the workshop, sits a
**clerk the owner hires**, who alone holds the record book's key, checks each request, and does the work
inside the locked room. By default each change waits for the foreman's approval; a worker can skip that
only with a pass the owner personally signed, and throwing things away always waits for approval.

Plain limits, said plainly: **this trusts the back office and the owner's own computer — it does not stop
someone on that computer who holds the clerk's key or the owner's signing pen.** It works only if the
lock described above is actually fitted. It hasn't yet been tried end-to-end with real workers on a real
site, it slows a worker who wants to try a change and test it straight away, and some kinds of workshop
(Copilot) aren't covered.
