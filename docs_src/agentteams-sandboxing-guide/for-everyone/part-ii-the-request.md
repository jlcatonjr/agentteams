# Part II — Asking for the room

<!-- skeleton:SB4 SB5 SB6 -->

You ask for the room by setting a single dial:

- **open** — no lock fitted; the worker is trusted.
- **locked** (the default) — the worker may only write on its own bench.
- **locked + curtained** — as above, and it also can't *read* the sensitive folders (passwords, keys) or
  peek at a neighbor's bench.

There is also a separate switch for the lock itself (`os_sandbox`). Turning it **off** removes the lock
while keeping your other settings; because that makes the room less safe, a workspace that requires
signatures will not do it until the owner signs off, and every other workspace prints a clear warning.
Turning it **on** fits the lock even on an "open" room.

A nice safety touch: if you *misspell* the dial, the system stops and asks — it never quietly leaves the
door open while pretending it's locked. And it refuses to hand you a "lock" for a kind of room it can't
actually lock, rather than give you a fake one.
