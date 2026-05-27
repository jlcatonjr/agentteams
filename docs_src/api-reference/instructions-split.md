# `instructions_split`

Cache-aware `CLAUDE.md` synthesis for the `copilot-vscode → claude` bridge. Replaces the bridge's default pointer-only `CLAUDE.md` with a layout that maximises Claude's prompt-cache hit rate: a stable preamble (canonical `copilot-instructions.md` bytes verbatim) followed by a dynamic boundary marker and a small attribution stanza.

Opt-in via [`--target-host-features bridge:copilot-vscode-to-claude:cache-split`](host-features.md).

## Layout

```
<cache header preamble>
<canonical copilot-instructions.md, bytes verbatim, rstripped>
SYSTEM_PROMPT_DYNAMIC_BOUNDARY
- Source: `.github/copilot-instructions.md`
- Source SHA-256: `<sha256 of canonical body>`
- Build timestamp (UTC): `<iso-8601>`
- Bridge: copilot-vscode → claude
- Stable-preamble cache contract: ...
```

The canonical body appears as a contiguous substring of the rendered file. Hand-editing the stable section is **not** supported — re-run the bridge with `--bridge-refresh` after editing the source.

## Public Surface

```python
render_cache_split(
    *,
    copilot_instructions: str,
    source_relative_path: str = ".github/copilot-instructions.md",
    build_timestamp: str | None = None,
) -> str
```
Render the full cache-split `CLAUDE.md` body. `build_timestamp` defaults to current UTC; pass an explicit value for reproducible test outputs.

```python
verify_equivalence(*, cache_split_text: str, original: str) -> bool
```
Confirm the rendered file contains `original` (rstripped) as a contiguous substring AND the boundary marker appears exactly once after it. Used by the Phase-7 regression contract.

```python
DYNAMIC_BOUNDARY_MARKER: str = "SYSTEM_PROMPT_DYNAMIC_BOUNDARY"
```
Public constant for downstream tools that split on the marker.

## Caveats

- The dynamic stanza embeds a build timestamp; the rendered file is intentionally non-deterministic across builds. Baselines (`tests/baselines/*.json`) scope to `.claude/` and do not include project-root `CLAUDE.md`.
- `verify_equivalence` is a containment check, not a hash equality check. A canonical source change shows up as drift only via the recorded `Source SHA-256` line.
