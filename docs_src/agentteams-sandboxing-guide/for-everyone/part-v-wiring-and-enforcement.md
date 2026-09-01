# Part V — Turning the key

<!-- skeleton:SB14 SB15 SB16 SB17 -->

> **Plain-language ceiling #2 — the lock does nothing until someone turns the key.** The system *hands
> you* a lock; it never installs it into your live setup for you. On Linux, "turning the key" means
> running the worker **through the doorkeeper**. A lock left on the bench locks nothing.

There's a quick way to check the key actually turned — a read-only inspection that reports "yes, it's
wired" or "no, it isn't," without ever reading your secrets.

And the **guard by the door**: for a handful of obviously dangerous actions (deleting things), the guard
stops and asks you first. Be clear-eyed about the guard, though — he watches a *specific list* of dangers
by the main door; he is a helpful speed-bump, **not a wall**. Plenty of side doors he doesn't watch. When
you've asked for the locked room, the guard is set to "if in doubt, stop"; in the default open room he's
set to "if in doubt, allow," so a jumpy guard never halts honest work.
