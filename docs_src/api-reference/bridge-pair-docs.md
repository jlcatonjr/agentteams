# `bridge_pair_docs`

Prose renderers for the bridge's framework-agnostic pair-dir artifacts. Carved out of
[`bridge`](bridge.md) for the CH-07 1000-line module ceiling (2026-08-11, open-items-backlog-
remediation plan); the renderers are re-exported from `agentteams.bridge`, so existing imports
keep working — the same pattern already established by [`bridge_skills`](bridge-skills.md) and
[`bridge_sources`](bridge-sources.md).

## Public Surface

```python
def _render_quickstart(source_framework: str, target_framework: str) -> str
def _render_entrypoint(source_framework: str, target_framework: str) -> str
def _render_domain_boundary(source_framework: str, target_framework: str) -> str
```

All three are module-private by name but re-exported through `agentteams.bridge` for the
emitter. Each returns the full text of one pair-dir artifact
(`references/bridges/<source>-to-<target>/{quickstart-snippet,entrypoint,domain-boundary}.md`).

## What stayed in `bridge.py`

`_render_target_files` (renders actual target-framework entry files like `CLAUDE.md` —
orchestration-coupled to `run_bridge`'s write path) and `_wrap_fence` (used only by
`_render_target_files`) were deliberately **not** moved here — this module is scoped to the
three framework-agnostic prose artifacts, not the target-framework-specific ones.

## The `generic` target's zero-tooling constraint

`--framework generic` (a bridge-only target for a consumer with no `agentteams` framework
adapter of its own) is the reason this module's functions branch on `target_framework`:
`_render_quickstart` and `_render_entrypoint` both swap their retrieval-guidance paragraph for
framework-neutral text when `target_framework == "generic"`, rather than instructing a
zero-tooling consumer to run `agentteams --query-index` (2026-08-10 finding, fixed 2026-08-11).
`_render_domain_boundary` deliberately received **no** such branch — it only describes the
three retrieval surfaces conceptually and never instructs the reader to run a command, so the
zero-tooling constraint doesn't apply to it (verified directly against the function body before
concluding it needed no change, not assumed).

`_render_quickstart` additionally special-cases `source_framework == "canonical"` paired with
`target_framework == "generic"`: suggesting a command to regenerate the canonical tree from
itself would be a degenerate, self-referential no-op.

## Related

- [`bridge`](bridge.md) — the emitter that calls these renderers and writes their output to disk
- [`bridge_sources`](bridge-sources.md) — source-side collection/inventory, carved earlier for
  the same CH-07 reason
- [`canonical`](canonical.md) — `DEFAULT_CANONICAL_SUBDIR`, referenced by `_render_quickstart`'s
  generic-target guidance
