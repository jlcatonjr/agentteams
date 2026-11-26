# `front_matter_widening`

Decides whether taking a template's front-matter value widens a capability grant (C-3), for
[`--reconcile-apply`](front-matter-reconcile.md). A divergence classified safe applies on the flag alone; a
widening needs a `reconcile-apply-widen` clearance ([CLI reference](../cli-reference.md#front-matter-reconciliation)).

The reader is deliberately strict, and separate from the merge engine's more lenient one: a list is read only
when every item in it is well formed, because an item a reader silently drops is a grant nobody reviewed.

> Source: `agentteams/front_matter_widening.py` (enforcement module, pinned in
> `references/enforcement-integrity.json`).

## Public Surface

```python
parse_items(key: str, value: str | None) -> tuple[str, ...] | None
```
The items of a capability list value in order: a quoted flow list (`['read', 'search']`), a bare comma list
(`Read, Grep`) or a block list (`- item` lines). `None` when any item is outside its shape (an unquoted item in
a flow list, a trailing comma, a nested bracket, an empty item, a comment), for an empty list (some frameworks
read an empty `tools` list as every tool), and for a scalar key such as `model`.

```python
classify(key: str, deployed: str | None, template: str) -> str
```
`"safe"` for a non-capability key, or a capability list whose template items are a subset of the deployed ones
(for the `disallowedTools` deny list, a superset). `"widening"` for everything else among capability keys: more
items, a key absent on disk, any `model` change, and any value `parse_items` cannot read.

```python
safe_part(key: str, deployed: str | None, template: str) -> str | None
```
The narrowing part of a widening list change, in the template's shape: for an allow list the items both sides
share (template order), for `disallowedTools` the union (template items, then the deployed ones it drops).
`--reconcile-apply` writes it when the widening is refused, so a removal never waits on a clearance. `None` when
either side is not a strictly read list, when it changes nothing, or when an allow list would be left empty
(Grok and Goose read an empty tools list as every tool, and Claude a missing grant), so that key is refused whole.

```python
value_digest(key: str, value: str) -> str
```
`sha256:<hex>` of the canonical value a widening would write: a strictly read list's sorted, de-duplicated items
(a reorder or a quoting change keeps the digest), otherwise its text with line endings and trailing whitespace
normalised. The `reconcile-apply-widen` grant token carries it, so a clearance covers only the value reviewed.
