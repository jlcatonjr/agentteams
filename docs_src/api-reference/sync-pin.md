# `sync_pin` — AgentTeamsModule

The **pinned-source contract** for multi-framework sync (multi-framework-pinned-sync plan).

> Source: `agentteams/sync_pin.py`

Owns the on-disk pin document only (locate / read / write). The reconciliation policy that
*uses* the pin lives in `multi_sync`.

The pin document at `<root>/.agentteams/pin.json` records the bootstrap pin (the framework that
seeds canonical and wins every conflict), the full sync set, the canonical hub location, and
the `last_synced_commit` anchor that drives commit-to-commit change detection.

---

## Public constants

- `PIN_SCHEMA_VERSION` (`"1.0"`): on-disk document version.
- `PIN_SUBPATH` (`".agentteams/pin.json"`): pin document location relative to the project root.
- `DEFAULT_CANONICAL_REL` (`".agentteams/canonical"`): default canonical hub location.

## Public functions

### `pin_path`

Return the pin document path for a project root.

### `read_pin`

Load the pin document, or `None` if the project is not pinned. Raises `ValueError` on a
malformed document.

### `save_pin`

Write (or overwrite) the pin document atomically. Raises `ValueError` if the pin is not in the
sync set.

### `update_last_synced_commit`

Advance the change-detection anchor after a successful sync. Raises `ValueError` if the project
is not pinned.
