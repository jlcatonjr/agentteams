# `memory_index_store` — Memory Index On-Disk Layout

> *Source: `agentteams/memory_index_store.py`*

Owns the bytes the committed [`memory_index`](memory-index.md) is stored as: a compact,
line-oriented JSON layout, and, above a size cap, native shards that the query path reads
directly. It never changes the index's data or schema.

## Why

The memory index is committed, and it outgrew GitHub's per-file limits (a warning above 50 MB, a
hard rejection above 100 MB). On a 56.1 MiB (58.8 MB) downstream index, measured 2026-10-15:

| Part of the file | Share |
|---|---|
| `postings` (term → `[{doc_id, tf}]`) | 74% |
| `indent=2` whitespace across the whole file | ~51% |
| stored paragraph text | 12% |

The payload was not the problem; the layout was. Each posting entry took six indented lines.

## Layout

- **Compact lines** (`serialize_index`). The same JSON object, written one line per top-level
  key, per document, per stored paragraph and per postings term. Plain JSON, so any
  `json.loads` reader is unaffected. About half the bytes of `indent=2` (56.1 MiB → 27.4 MiB on
  the index above), and still line-diffable. Paragraphs keep one line each, so the line-based
  content scanner sees the same units as before.
- **Shards** (`encode_memory_index`). When the compact text is larger than the cap (default
  20 MiB; set `AGENTTEAMS_MEMORY_INDEX_SHARD_MAX_BYTES` to change it, minimum 1024),
  the documents and then the postings terms are packed, in order, into
  `references/memory-index.part-0001.json`, `…-0002.json`, … Each part is a valid JSON object
  (`artifact_type: "memory-index-part"`) under the cap. `references/memory-index.json` becomes a
  small manifest (`artifact_type: "memory-index-shards"`) that holds the index metadata and each
  part's file name, byte size and sha256. An index below the cap stays one file.

```text
references/
  memory-index.json            1.5 KB  manifest: metadata + parts[{file, bytes, sha256}]
  memory-index.part-0001.json  20 MiB  documents …, then postings terms …
  memory-index.part-0002.json   7 MiB  … remaining postings terms
```

## No reassembly, no git hooks

`--query-index`, `--refresh-index`, the stale check and the recall MCP server all load the index
through `read_memory_index_payload`, which reads the parts directly. There is no reassembly step
after a pull, so no `post-merge` / `post-checkout` hook is needed. A hook-based design would fail
exactly where it matters: hooks do not run in CI, on GitHub, or in a fresh clone until someone
installs them.

## Failing safe

Reading verifies every part against the manifest before parsing. A missing, truncated,
merge-conflicted or edited part raises `MemoryIndexError` naming the part and the fix
(`run agentteams --refresh-index to rebuild it`). It never returns a partial index, which would
quietly answer from a subset of the corpus. The manifest can name only
`memory-index.part-NNNN.json` files in its own directory, numbered 1..N in order, so it cannot
point a reader at another file. The recall MCP server also passes its project-confinement check
for each part.

A reader that loads the manifest between a writer's shard writes and its manifest write sees a
sha256 mismatch. The read is retried once from a fresh manifest before it fails.

Hashes are taken over the bytes with every `\r` removed. The layout only puts newlines between
JSON tokens (string values escape theirs), so a carriage return can only be a line ending added
by Windows or `core.autocrlf`, and a Windows checkout still verifies.

## Compatibility and migration

- **Existing single-file indexes** (`indent=2`) read unchanged. The next `--refresh-index` or
  `--update` rewrites them in the compact layout, sharded if they exceed the cap.
- **Migration is a rebuild.** Writing removes any `memory-index.part-NNNN.json` the new manifest
  does not list (exact names only, in that one directory), so shrinking below the cap returns
  to one file with no leftovers.
- **Readers that predate sharding** (older agentteams installs, ad-hoc scripts) see the
  manifest at `memory-index.json`. The index schema pins `artifact_type` to `memory-index`, so
  they reject it with a schema error instead of treating it as an empty index. Upgrade them,
  or raise the cap to keep the file whole.
- **Incremental `sed` updates** (`AGENTTEAMS_MEMORY_INDEX_INCREMENTAL_SED=1`,
  [`memory_index_incremental`](memory-index-incremental.md)) patch the `indent=2` layout line
  by line. They run only on a legacy-layout file; the compact and sharded layouts take the full
  rebuild, which is always correct.
- **Merge conflicts.** Do not hand-merge the index. Take either side and run
  `agentteams --refresh-index`. A conflicted part fails its sha256 check, so a hand-merge that
  goes wrong is caught.

The content scanner exempts parts from its absolute-path and entropy checks on the same terms
as `memory-index.json`: the exact name pattern, directly in the agents directory's
`references/`. Injection and credential-pattern checks still run on them. Fleet runs treat the
parts as volatile, like the index itself.

## API

### `serialize_index(index)`

Render an index in the compact layout. Deterministic.

**Args:** `index` (`dict[str, Any]`) — a memory index payload.
**Returns:** `str` — JSON text ending in a newline.

### `encode_memory_index(index, *, max_shard_bytes=None)`

Render an index to the files that store it.

**Args:** `index` (`dict[str, Any]`); `max_shard_bytes` (`int | None`) — cap for one file;
`None` reads the environment (`max_shard_bytes_from_env`).
**Returns:** `EncodedIndex` — `.files` (`(name, text)` pairs in write order, manifest last),
`.chunks` (bytes for the validation-cache key) and `.sharded`.
**Raises:** `MemoryIndexError` — more than 9999 parts would be needed.

### `write_encoded_index(index_path, encoded)`

Write each file atomically (parts first, `memory-index.json` last), then remove parts the
manifest no longer lists.

**Args:** `index_path` (`Path`) — path of `memory-index.json`; `encoded` (`EncodedIndex`).
**Returns:** `list[Path]` — the paths written.
**Raises:** `OSError` — a file could not be written.

### `read_memory_index_payload(index_path, *, confine=None)`

Load a single-file or sharded index.

**Args:** `index_path` (`Path`); `confine` (`Callable[[Path], Path] | None`) — applied to each
part path before reading; returns the path to read or raises to refuse it.
**Returns:** `tuple[dict, list[bytes]]` — the index, and the normalized bytes for the
validation cache.
**Raises:** `MemoryIndexError` — the file or a part is missing, unreadable, corrupt, or does not
match the manifest.

### Helpers

- `part_file_name(number)` → `memory-index.part-NNNN.json` (raises `ValueError` outside 1..9999).
- `is_part_file_name(name)` → whether a bare name is a part name.
- `is_legacy_layout(index_path)` → whether the file is a single-file `indent=2` index.
- `max_shard_bytes_from_env()` → the cap in bytes.

## See also

- [`memory_index`](memory-index.md) — building and querying the index.
- [`memory_index_incremental`](memory-index-incremental.md) — the opt-in legacy-layout patcher.
- Tests: `tests/test_memory_index_store.py`.
