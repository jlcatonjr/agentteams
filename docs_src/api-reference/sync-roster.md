# `sync_roster` — AgentTeamsModule

Carries the pin's added and removed agents through a [pinned sync](multi-sync.md).

> *Source: `agentteams/sync_roster.py`*

Under the operator ruling of 2026-11-04 the pin decides, and that now covers the roster. Before, every
roster difference was only logged: an agent the pin deleted came back on the next sync, because the
hub still held it and projection wrote it out again, and an agent the pin added never reached the hub.

| Change | Result |
|--------|--------|
| The pin adds an agent | It joins the hub and projects to every framework (`applied (pin added agent)`). |
| The pin deletes an agent, confirmed | It leaves the hub, and every framework's copy is deleted after the pre-projection backup (`applied (pin removed agent)`). |
| The pin deletes an agent, not confirmed | Logged as `proposed (unconfirmed pin deletion; restored)`; projection restores the pin's file. |
| A peer adds an agent | Kept in that peer only, and logged as `proposed (peer-only agent ...)` on every pass. |
| A peer deletes an agent | Projection restores it (`restored (peer removed agent ...)`). |
| The pin has no agents and the hub has some | `--sync` and `--sync-init` refuse before writing. |
| `--sync-init` over an existing hub | After a successful projection, the hub drops the agents the new pin lacks. Other frameworks keep their copies, because the earlier pin may have been a different framework. |

A deletion is confirmed when the pin's agents directory is tracked by git and the agent's file is gone
from `HEAD`, or when every other framework in the sync set is present and none has the agent any more (a
framework whose agents directory is missing, or a sync set of the pin alone, leaves only git). Anything
else fails closed: no
repository, no `HEAD`, a git error, an untracked or gitignored agents directory, or an uncommitted
deletion. To remove an agent from a team whose pin directory git doesn't track, delete it from every
framework, then sync.

Each framework's `.agentteams-backups` keeps its deleted copy, so `--restore-backup` recovers it, but only
the newest 10 backups are kept and every sync adds one. The hub's copy gets no backup: git history keeps
it only if `.agentteams/canonical` is committed. In a tracked pin directory, git keeps the pin's.

## Public functions

### `apply_pin_roster`

`apply_pin_roster(canonical_cai, pin_cai, pin_baseline, *, root, pin_agents_dir, peer_slugs) -> (canonical_cai, records, removed)`

Adds the pin's new agents to the hub's CAI dict and drops the ones the pin removed. A removal is an
agent in the pin's baseline that the pin no longer has, with the deletion confirmed (`peer_slugs` is every
slug another framework still has). It is worked out from the baseline rather than the hub, so a pass that
fails after the hub changed still removes the peers' copies next time. Returns the updated dict, one
`(slug, resolution, note)` record per change, and the removed slugs. Raises `ValueError` through
`refuse_empty_pin` before changing anything.

### `refuse_empty_pin`

`refuse_empty_pin(canonical_cai, pin_cai, pin_agents_dir) -> None`

Raises `ValueError` when the pin exports no agents (an empty, wrong or unreadable directory) while the
hub has some.

### `prune_hub_agents`

`prune_hub_agents(canonical_dir, canonical_cai, *, dry_run) -> list[str]`

Deletes the hub's `agents/<slug>.md` files for agents no longer in `canonical_cai`. The hub's agent list
is rebuilt from those files (`canonical.load_canonical`), so dropping an agent from the dict alone does
not remove it.

### `remove_dropped_peer_agents`

`remove_dropped_peer_agents(agents_dir, peer_cai, dropped, *, dry_run) -> list[str]`

Deletes one framework's agent files for the `dropped` slugs. A peer-only agent is never in `dropped`. A
symlink, or a file that resolves outside the agents directory, is never deleted. Under `dry_run` it only
reports, so a dry `--sync` lists each framework's removals in its notices.
