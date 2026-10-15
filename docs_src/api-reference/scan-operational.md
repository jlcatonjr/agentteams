# `scan_operational` — Scanner Exemption for Pipeline-Written JSON

> *Source: `agentteams/scan_operational.py`*

Decides which pipeline-written JSON files the content scanner ([`scan`](scan.md)) exempts from
its absolute-path, entropy and unresolved-placeholder detectors. Injection and pattern-based
credential checks always run. Carved from `scan.py` (CH-07) and **integrity-pinned** with it in
`references/enforcement-integrity.json`: an exemption decides what the scanner does not look
at, so an unreviewed widening here blinds it as surely as an edit to the scanner. The module is
stdlib-only and imports nothing from the package, so its decision cannot be widened from an
unpinned module.

## Rule

A file is exempt when **both** hold:

1. Its name is one of `_OPERATIONAL_JSON_NAMES` (`build-log.json`, `delivery-receipt.json`,
   `memory-index.json`, `eval-suite.json`, `doc-hashes.json`,
   `security-vulnerability-watch.json`), **or** it is a memory-index shard
   (`memory-index.part-NNNN.json`) that the `memory-index.json` beside it lists, as a shard
   manifest, with the sha256 of the shard's current bytes.
2. It resides directly in the scanned agents directory's `references/` (after resolving
   symlinks).

The manifest requirement is Rule 12: exempting the shard name pattern alone would hand up to
9999 new exempt names to anyone who can write to `references/`. A file that only matches the
pattern, or a listed shard edited after the index was written, is scanned normally.

Accepted residue: whoever can write `references/` can also write a manifest that lists a planted
shard with its correct hash. That adds nothing they lacked, since `memory-index.json` itself is
exempt by name and could carry the same payload. Injection and credential checks run either way.

## API

### `_is_operational_json(file_path, agents_dir)`

**Args:** `file_path` (`Path`) — the file being scanned; `agents_dir` (`Path`) — the scanned
agents directory.
**Returns:** `bool` — whether the three detectors are skipped for it.

### `_is_listed_memory_index_part(file_path)`

**Args:** `file_path` (`Path`) — a file whose name matches the shard pattern.
**Returns:** `bool` — whether the sibling shard manifest (at most 1 MiB) lists it with its
current sha256 over CR-stripped bytes. Any read or parse failure returns `False`.

`scan.py` re-exports `_OPERATIONAL_JSON_NAMES`, `_OPERATIONAL_JSON_DIR` and
`_is_operational_json`, so existing imports resolve unchanged.

## See also

- [`scan`](scan.md) — the scanner.
- [`memory_index_store`](memory-index-store.md) — writes the shards and their manifest.
