# Part IV — The three kinds of lock

<!-- skeleton:SB10 SB11 SB12 SB13 -->

There are three kinds of lock, one per situation:

- **The Claude lock** — a setting you switch on in the Claude tool. (One nuance told honestly: this lock
  handles *what the worker can touch*, but it leaves *phoning out* to the Claude tool's own default.)
- **The Goose-on-Mac lock** — a Mac-specific lock that also blocks phoning out by default.
- **The Linux lock** — a small, self-contained **doorkeeper** the system hands you: run the worker
  *through* it and it enforces the locked room (and, always, hides the password folders).

On Linux, the Linux doorkeeper works for **any** worker — the system doesn't play favorites between AI
tools. But (see the next part) a lock only matters once someone fits it to the door.
