# Part IX — Two locks, and who may open them

<!-- skeleton:SB25 SB26 SB27 SB28 SB29 SB30 -->

So far this guide has talked about one lock: the locked **room** the worker stands in. There is now a
second, separate one: a sealed **glovebox** on the bench. When the worker wants to try something risky,
such as running a piece of code, it puts its hands through the gloves and does it inside the box. Whatever
happens in the box stays in the box: it has no window to the outside, no tools from the rest of the
workshop, and a timer that stops the work if it runs too long.

## Two switches

- **The room lock** (the OS sandbox) keeps the *worker itself* inside the room. For most teams it is on
  by default, and it can be switched off on its own.
- **The glovebox** (the WASM sandbox) holds the *work the worker does by hand*. It is **off unless you
  turn it on.** One kind of team, where every worker is built to live inside gloveboxes, has it on
  automatically.

They work best together. The glovebox alone still keeps the risky work sealed, but a worker who is not in
a locked room could walk over and change the glovebox's settings, so the system never calls that
combination "enforced". On Windows computers there is no room lock to offer, so only the glovebox is
available there.

## Opening a lock needs the owner's signature

Turning either lock off, letting a glovebox see more of the workshop, or letting a worker keep using its
bare hands while the gloveboxes are on, all make the workshop less safe. In most workspaces the owner must
sign off first, with a signature only the owner can make; that requirement is **on by default**. In the
few workspaces where it is not on, the system prints a clear warning and writes the change down.

## Gloveboxes for workers from other companies

Many workers come from outside suppliers (the AI tools from different companies). Those workers cannot
step inside a glovebox themselves, but they can be handed one to use. The system offers the glovebox to
each worker that does hands-on work, and takes away their bare-hands option where the supplier's tools let
it. Two kinds of worker keep their bare hands by default because their job needs them (the ones that look
after the project's history files), and the foreman always does.

How sure can you be that a given worker really uses the glovebox? It depends on the supplier, and the
system says so plainly for each one. For one supplier it can confirm it, but only once the room lock is on
and the owner has fitted the room's extra locks. For a second it can confirm it for workers sent in
directly, not for helpers they bring along. For a third it depends on how the worker is let in. For the
rest it is only advice: the glovebox is offered, and the worker is asked to use it, but nothing stops
it from working by hand.

## What the glovebox does not do

- **It is not a full workshop.** Inside the glovebox the worker can run code, but not the workshop's big
  machines (the everyday developer tools). A worker who needs those keeps its bare hands, with the
  owner's signature.
- **It does not lock up the worker,** only the work done through the gloves. Locking up the worker is
  still the room lock's job.
- **Where the system says "advice only", it is advice only.**
- **It does not stop someone who already holds the building's master key.**

The gloveboxes and their parts are only fetched when the owner asks, and each part is checked against a
known fingerprint before use; if it does not match, nothing runs.
