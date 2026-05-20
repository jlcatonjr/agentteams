# `fence_inject` — AgentTeamsModule

Retrofit fence markers into legacy markdown files for incremental update compatibility.

Wraps a legacy file's body with canonical `AGENTTEAMS:BEGIN`/`END` fence markers so it becomes eligible for merge-mode `--update` runs without requiring `--overwrite`. Used during cross-repository target remediation when legacy files lack fence metadata.

> *Source: `agentteams/fence_inject.py`*

---

## Constants

### `DEFAULT_RETROFIT_FENCE_ID`

> *Source: `agentteams/fence_inject.py`*

Default fence section ID used when retrofitting a legacy file.

**Type:** `str`  
**Value:** `"content"`

**Design note:** Matches the fence ID that `emit._normalize_generated_content` uses for whole-body wraps, so a later `--update --merge` cleanly replaces in-place without duplication.

---

## Classes

### `InjectResult`

> *Source: `agentteams/fence_inject.py`*

Outcome of a fence marker injection attempt.

**Attributes:**

- `output_path` (`Path | None`) — Path of the written file (sidecar or in-place). `None` if the call was a no-op (already fenced).
- `injected` (`bool`) — `True` if markers were added; `False` if no-op (idempotent).
- `backup_path` (`Path | None`) — Set only on successful in-place injection. `None` for sidecar mode or no-ops.
- `fence_id` (`str`) — The fence ID used (base + numeric suffix if collision detected).

---

## Functions

### `inject_fence_markers(path, *, mode="sidecar", confirm_in_place=False, fence_id=None)`

> *Source: `agentteams/fence_inject.py`*

Add canonical `AGENTTEAMS:BEGIN`/`END` markers to a legacy markdown file.

**Args:**

- `path` (`Path | str`) — Path to the legacy file to process
- `mode` (`str`, keyword-only) — `"sidecar"` (default) or `"in-place"`. Default: `"sidecar"`
- `confirm_in_place` (`bool`, keyword-only) — Required for `mode="in-place"` (safety gate). Default: `False`
- `fence_id` (`str | None`, keyword-only) — Custom fence ID (must match `[a-z][a-z0-9_]*`). Default: uses `DEFAULT_RETROFIT_FENCE_ID`

**Returns:** `InjectResult` — Result of the injection operation

**Behavior:**

- **Idempotent:** If the file already contains any AGENTTEAMS fence markers, this is a no-op; `injected=False` and `output_path=None`
- **Sidecar mode:** Writes to `<original-name>.fenced.md` alongside the source; never modifies the original
- **In-place mode:** Modifies the original file; requires `confirm_in_place=True` and creates a timestamped `.agentteams-backups/` backup before mutating
- **Fence ID collision:** If the requested `fence_id` already exists in the file, appends a numeric suffix (e.g., `content_1`, `content_2`)
- **YAML front matter preservation:** If the file starts with YAML front matter, it is kept above the BEGIN marker

**Raises:**

- `ValueError` — If `fence_id` doesn't match `[a-z][a-z0-9_]*`
- `FileNotFoundError` — If the target file doesn't exist
- `ValueError` — If `mode="in-place"` but `confirm_in_place=False`

---

## Typical Usage

```python
from pathlib import Path
from agentteams.fence_inject import inject_fence_markers

# Legacy file without fence markers
legacy_file = Path("target-team/.github/agents/my-agent.md")

# Option 1: Create a sidecar version first to review
result = inject_fence_markers(legacy_file, mode="sidecar")
if result.injected:
    print(f"✓ Fenced version: {result.output_path}")
    # Review, then rename/overwrite if satisfied

# Option 2: Retrofit in-place (requires explicit confirmation)
result = inject_fence_markers(
    legacy_file,
    mode="in-place",
    confirm_in_place=True  # Must pass this gate
)
if result.injected:
    print(f"✓ Backed up to: {result.backup_path}")
    print(f"✓ Retrofitted: {result.output_path}")
```

---

## Integration with `--update`

Once a file is retrofitted with fence markers, it becomes eligible for merge-mode updates:

```bash
# Before retrofit: must use --overwrite (requires security clearance)
python build_team.py --update --overwrite --yes

# After retrofit: can use --update --merge (preserves user-authored content)
python build_team.py --update --merge --yes
```

This is particularly useful for cross-repository target updates where legacy agents lack fence markers.

---

## Design Rationale

- **D-1 (Safety-first):** Single fence region wrapping the entire body (heuristic per-section detection out of scope)
- **D-2 (Naming convention):** Default fence ID is `content` (matches `emit._normalize_generated_content` for clean in-place replacement)
- **D-3 (Sidecar default):** Sidecar mode is default; in-place requires explicit confirmation + `@security` review
- **D-4 (Not auto-invoked):** Separate CLI surface; not automatic during `--update`
