# Part I — What it is & why

<!-- skeleton:SB1 SB2 SB3 -->

Imagine hiring a brilliant, tireless worker who follows written instructions **exactly** — even a note a
stranger sneaks onto the workbench. That's an AI agent. To keep a bad instruction from spilling out, you
can put the worker in a **locked room**: at minimum it can only *write* on its own bench. Ask for the
stronger "curtained" room and it also can't *read* your private files; and the strongest lock (on Linux)
also cuts the phone line. (Which protections you get depends on how much you ask for — see
[Part IV](part-iv-the-mechanisms.md).)

Two things do the guarding: the **locked room** (what the worker can touch and reach) and a **guard by
the door** who can shout "stop!" at a few obviously dangerous actions.

Important, and easy to forget: this room is only for the worker who **builds** your product. It is **not
inside the product you deliver** — the thing you ship to your own customers needs its own protections.

> **Plain-language ceiling #1 — the room now comes with a lock fitted, but you still have to turn the
> key.** By default the worker's room comes with a lock *provided* — but the lock isn't turned until you
> wire it in, so nothing is actually held back yet, and the guard by the door still says "if in doubt,
> allow" unless you deliberately ask for the locked room. Set the dial to "open" if you'd rather no lock
> be fitted at all.
