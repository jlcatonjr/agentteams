# Part IV — The three kinds of lock

<!-- skeleton:SB10 SB11 SB12 SB13 -->

There are three kinds of lock, one per situation:

- **The Claude lock** — a setting you switch on in the Claude tool. (One nuance told honestly: this lock
  handles *what the worker can touch*, but it leaves *phoning out* to the Claude tool's own default.)
- **The Goose-on-Mac lock** — a Mac-specific built-in lock that also blocks phoning out by default.
- **The doorkeeper lock** — a small, self-contained **doorkeeper** the system hands you: run the worker
  *through* it and it enforces the locked room (and, always, hides the password folders). It works on
  **both Linux and Mac** for **any** worker — the system doesn't play favorites between AI tools. On
  Linux it uses the system's `bwrap`; on Mac it uses the Mac's `sandbox-exec`.

Two honest notes on the **Mac** doorkeeper (added recently): it can't cap memory, it doesn't filter the
deepest system calls, and its "block dangerous programs" list is a helpful extra, not an ironclad one.
And on either system, a lock only matters once someone fits it to the door (next part).
