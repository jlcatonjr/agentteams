# `capability_map` — AgentTeamsModule

Canonical tool-scope vocabulary and framework↔canonical capability mapping for the CAI interop pipeline (Phase C of the durable-canonical-agent-format plan).

> *Source: `agentteams/capability_map.py`*

---

## Canonical Vocabulary

### `CANONICAL_TOOL_SCOPES`

The seven canonical tool-scope tokens, in stable order:

1. `read`
2. `edit`
3. `search`
4. `execute`
5. `todo`
6. `agent`
7. `retrieval`

This matches the forward map in [`agentteams/frameworks/claude.py`](frameworks.md) (`_VSCODE_TO_CLAUDE_TOOLS`), which remains the forward authority for Claude rendering.

---

## Public Functions

### `canonical_tools_for_copilot_vscode(content)`

Extracts the canonical tool scopes declared in a copilot-vscode agent file's YAML front matter (`tools:` list). Returns the tokens in canonical order, or `None` when no front matter is present, no `tools:` key is present, or `tools:` uses YAML block-list style (e.g. `tools:\n  - read`) rather than the inline bracket form (`tools: [read, edit]`) the extractor matches. Unknown tokens are ignored; the result is identity-plus-validation.

### `claude_allowed_tools_to_canonical(claude_tools)`

Reverse-maps Claude tool names (`Read`, `Edit`, `Write`, `Grep`, `Glob`, `Bash`, `TodoWrite`, `Task`, and the scoped `Bash(python -m agentteams.research:*)`) to canonical tokens. Reproduces Claude's dedup rule: when both `execute` and `retrieval` are implied, `retrieval` is dropped because unrestricted `Bash` subsumes the scoped retrieval grant.

### `canonical_tools_for_claude(content)`

Extracts the `tools:` (current key since 2026-08-06) or legacy `allowed-tools:` front-matter line from a Claude agent file and reverse-maps it via `claude_allowed_tools_to_canonical`. Bracket-form lists are skipped. Returns `None` when absent or empty.

### `goose_extensions_to_canonical(extension_names)`

Best-effort mapping from goose extension names to canonical tokens: `developer` implies `read`, `edit`, `search`, `execute`; `todo` implies `todo`. There is no goose extension for `agent` (handoffs travel through recipe structure) or `retrieval` (shell execution of `python -m agentteams.research` through developer tooling).

### `canonical_to_goose_extensions(tokens)`

Inverse coarse mapping: canonical tokens fold back into the goose extensions that provide them (`developer`, `todo`). Multiple tokens may map to the same extension; results are deduplicated and ordered.

### `capabilities_from_tokens(tokens, framework)`

Builds the CAI `capabilities` object: `{"tool_scopes": [...]}` when tokens are present, else `{}`. Framework-agnostic today; the parameter reserves room for framework-specific capability channels.

---

## Design Notes

- copilot-cli and agents-md have no native capability channel; their capabilities travel only via the CAI `raw` escape hatch.
- All front-matter extraction delegates to the shared scanner `agentteams.yaml_frontmatter.parse_yaml_front_matter` (Phase B consolidation).
- `export_to_cai` / `import_from_cai` in [`agentteams/interop.py`](interop.md) are the primary callers; `agentteams/frameworks/goose.py` also calls `goose_extensions_to_canonical` and `capabilities_from_tokens` directly when parsing a native goose recipe (`parse_agent_source`). Capabilities survive round-trips as typed CAI data.

---

## See also

- [`canonical`](canonical.md) — materializes the canonical tool-scope vocabulary into agent front matter.
- [`sync_classifier`](sync-classifier.md) — routes capability-bearing fields to human review via the §6.1 carve-out.
