# `eval_adapters` — AgentTeamsModule

Framework adapter generators for behavioral evaluation artifacts.

These adapters translate the framework-neutral suite from `agentteams.eval_suite` into framework-specific runnable artifacts. Adapter modules are code generators and intentionally avoid runtime dependencies on target eval frameworks.

> Source: `agentteams/eval_adapters/`

---

## Module: `inspect_ai`

> Source: `agentteams/eval_adapters/inspect_ai.py`

Builds Inspect AI task-module source text from a neutral eval suite.

### `render_inspect_ai_module(suite)`

Return generated Python module source containing one Inspect AI task per scenario.

Args:

- `suite` (`dict[str, Any]`) — Framework-neutral eval suite.

Returns: `str` — Generated Inspect AI task module source.

Behavior notes:

- Pure function; does not mutate `suite`.
- Emits deterministic task identifiers with safe-name normalization.
- Encodes a structural scorer for neutral predicate kinds:
  - `frontmatter-list-contains-all`
  - `agent-count`
  - `handoff-chain`
  - `frontmatter-and-body`
- Generated runtime expects `AGENTTEAMS_TEAM_DIR` to locate team outputs.

### `write_inspect_ai_module(suite, path)`

Write generated Inspect AI module source to `path`.

Args:

- `suite` (`dict[str, Any]`)
- `path` (`Path | str`)

Returns: `Path` — Written file path.

---

## Module: `openai_evals`

> Source: `agentteams/eval_adapters/openai_evals.py`

Builds OpenAI-Evals-shaped JSON definitions from a neutral eval suite.

### Constants

- `OPENAI_EVALS_DEFINITION_VERSION` — definition schema/version marker used in generated JSON.
- `STRUCTURAL_GRADER_CLASS` — class-path reference expected by downstream OpenAI Evals integration.

### `build_openai_evals_definition(suite)`

Return OpenAI-Evals-shaped definition data for a neutral suite.

Args:

- `suite` (`dict[str, Any]`) — Framework-neutral eval suite.

Returns: `dict[str, Any]` — Structured OpenAI Evals definition payload.

Behavior notes:

- Pure function; does not mutate `suite`.
- Preserves scenario predicates in sample metadata.
- Uses `AGENTTEAMS_TEAM_DIR` convention for downstream team-dir resolution.

### `render_openai_evals_definition(suite)`

Return deterministic JSON text for the OpenAI Evals definition.

Args:

- `suite` (`dict[str, Any]`)

Returns: `str` — Pretty JSON with trailing newline.

### `write_openai_evals_definition(suite, path)`

Write rendered OpenAI Evals JSON to `path`.

Args:

- `suite` (`dict[str, Any]`)
- `path` (`Path | str`)

Returns: `Path` — Written file path.

---

## Adapter Contract Notes

- Neutral core first: build suite with `agentteams.eval_suite`.
- Adapter second: convert neutral suite to framework-specific artifact.
- Runtime coupling stays downstream: adapter modules do not require Inspect AI or OpenAI Evals packages at import time.
