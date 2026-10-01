# `learned_blocks` — AgentTeamsModule

Pure-text parse, compose and verify of an agent file's **learned block** — the one region
[`agent_doc_sync`](agent-doc-sync.md) propagates between framework copies. Never touches the
filesystem.

> *Source: `agentteams/learned_blocks.py`* (integrity-pinned)

## Host formats

- **markdown** (`.github/agents/*.agent.md`, `.claude/agents/*.md`): the markers are whole
  column-0 lines in the body — never in YAML front matter, never inside an `AGENTTEAMS:BEGIN/END`
  template fence. A file without a block gets one appended at its end.
- **recipe** (`.goose/recipes/*.yaml`): the block lives inside the `instructions: |` literal block
  scalar at that scalar's indent. Canonical content has the indent removed, so the same notes
  digest identically in every framework. A line indented less than the scalar (the dedent case,
  which would end the scalar) is refused. A file without a block gets one after the scalar's last
  non-blank line.

## Public constants

- `BEGIN_MARKER`, `END_MARKER`: the marker lines (without indent or newline).
- `MARKDOWN`, `RECIPE`: host format ids.

## Public classes

### `LearnedBlockError`

Raised when a block (or its host structure) cannot be handled safely.

### `LearnedBlock`

`content` (canonical), `start`/`end` offsets, and the `digest` property (sha256 of `content`).

### `ParsedDoc`

`kind`, `block` (or `None`), `insert_at`, `indent`, `fm_end`.

## Public functions

### `content_digest(content)`

The sha256 hex digest of canonical block content.

### `parse_markdown(text)`

Locate the block of a markdown agent file. Raises `LearnedBlockError` on unterminated front
matter, more than one block, a non-column-0 marker, or a block in front matter or a fence.

### `parse_recipe(text)`

Locate the block inside a recipe's `instructions: |`. Raises `LearnedBlockError` when the scalar
is missing or not plain, a marker is outside it or off its indent, a block line dedents, or the
block sits in a fence.

### `parse(text, kind)`

Dispatch on `kind`.

### `content_problems(content)`

Why content may not be propagated: a fence-family token (`AGENTTEAMS:`, `AGENTTEAMS-LEARNED:`, …),
a line break or control character other than `\n` (CR, VT, FF, NEL, LS, PS, …), or a missing
final newline.

### `render_block(content, indent)`

The marker lines plus content at `indent` (empty lines stay empty).

### `compose(text, parsed, content)`

`text` with its block replaced by — or extended with — `content`.

### `top_level_sections(text)`

Each top-level recipe key mapped to its raw text (duplicates get a `#n` suffix).

### `verify_composed(old, new, kind, content)`

Prove `new` differs from `old` only inside the block: the re-parsed block carries `content`;
bytes outside it, the template fence set and the front matter are unchanged; for recipes the
structural check result and every top-level key except `instructions` are unchanged. Returns the
list of problems (empty means safe to write).
