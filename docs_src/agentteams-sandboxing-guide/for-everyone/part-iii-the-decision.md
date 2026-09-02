# Part III — Which buildings have a lock

<!-- skeleton:SB7 SB8 SB9 -->

Not every building can be locked the same way. When you ask for the locked room, the system checks the
building (the operating system) and the worker (which AI tool):

- On **Linux** buildings, there's a lock for **any** worker.
- On **Mac** buildings, there's now a lock for **any** worker too (added recently) — the Claude and
  Goose workers use their own built-in Mac locks; every other worker uses the same doorkeeper-lock as on
  Linux. (Important: the Mac lock has **not been tested against real break-ins yet** — see Part VII.)
- The **Claude** worker brings its own lock everywhere.
- On **Windows** and anything else, there's **no lock to hand out** — and rather than pretend, the
  system **refuses** and says so (unless you insist, in which case it proceeds but tells you plainly
  there is no lock here).

On Linux **and Mac** there's one more honest note the system gives you: "here is the lock, **but you have
to fit it to the door yourself** — it isn't automatic." That warning exists so nobody *thinks* they're
locked in when the lock is still sitting on the bench.
