# Goose tool-scoping spike (Goose 1.37.0, 2026-10-05/06)

Context: the Goose read-only-agents plan (phase 0); consumed by `agentteams/frameworks/goose_tool_scoping.py`. Goose **1.37.0** (`/opt/homebrew/bin/goose`).

**Method.** This is the mathAgents stub-model method, re-implemented with the scripts kept (see `scripts/goose-probe/`):
- `goose run --recipe <r> --no-session` runs against a local stub of the OpenAI chat API (`stub.py`), with an isolated `HOME` and `GOOSE_MODE=auto`.
- The stub logs the tool list Goose offers the model, requests one named tool call, then ends the turn.
- `probe_mcp.py` is a stdio MCP server with `read_probe` (harmless) and `write_probe` (creates `PROBE_WRITTEN`).
- The work directory is fingerprinted (file list + content hashes) before and after each run.

| Case | Recipe extensions | Tools offered to the model | Requested call | Result |
|---|---|---|---|---|
| R2 (positive control) | stdio `probe`, no `available_tools` | `probe__read_probe`, `probe__write_probe` | `write_probe` | **executed**: `PROBE_WRITTEN` created |
| R1 | stdio `probe`, `available_tools: [read_probe]` | `probe__read_probe` only | `write_probe` | **refused at dispatch**: `-32002: Tool 'write_probe' not found. Available tools: [probe__read_probe]`; no file |
| R1b | same as R1 | `probe__read_probe` | `read_probe` | executed (`read-ok`) |
| R4 | platform `analyze` only | `analyze` | `analyze path=.` | structure only (`1 files, 2L, 1F`); work dir byte-identical |
| R5 | platform `analyze` only | `analyze` | `analyze` on a file **outside** the workspace | **executed**: returned `secrets.py [3L, 1F]`, `F: leak:2` (structure, no contents) |

## Answers

1. **`available_tools` is enforced on stdio MCP extensions in Goose 1.37.0, not only on builtin and
   platform extensions.** A withheld tool is not offered and is refused at dispatch. The plan's phase-1
   design (a first-party stdio server, scoped by `available_tools` as a second wall) holds.
2. **A recipe with no `developer` runs**, and Goose offers exactly that recipe's extension tools. It adds
   no implicit `analyze` or `developer`.
3. **`analyze` is read-only**: its schema has no write parameter, and runs leave the work directory
   unchanged. It never returns file contents.
4. **New: `analyze` is not confined to the workspace.** It reports structure (line, function and class
   counts; symbol names) for any path the user can read. **Plan change:** read-only agents get
   `agentteams_readfs` (realpath-confined) and **not** `analyze` by default. If `analyze` is wanted, it must
   run under the `exclusive` sandbox profile (`deny file-read*` of the protected set).

## Follow-up spikes (asked by mathAgents, same day)

| Case | Recipe extensions | Offered | Requested call | Result |
|---|---|---|---|---|
| A0 | `developer` with `available_tools: [tree]`; `analyze` not listed | `analyze`, `tree` | `analyze` | **executed**: Goose adds `analyze` beside `developer` when the recipe does not list it |
| A1 | as A0, plus `analyze` with `available_tools: []` | `analyze`, `tree` | `analyze` | **executed**: an empty allowlist means *no restriction*, not "nothing" |
| A2 | as A0, plus `analyze` with `available_tools: [__none__]` | `tree` | `analyze` | **refused**: `Tool 'analyze' not found`. A non-matching allowlist disables an extension |
| B1 | as A2 | `tree` | `tree` on a directory **outside** the workspace | **executed**: listed `secrets.py  [3]` (names and line counts) |

5. **To disable an auto-added extension, list it with a non-matching allowlist** (`[__none__]`). Never use
   `[]`, which is unrestricted. A non-matching allowlist fails closed, unless Goose ever ships a tool by that name.
6. **`developer.tree` is not workspace-confined either.** Every Goose 1.37 builtin path tool (`tree`,
   `analyze`, `shell`) can reach outside the workspace. Only a first-party server (`agentteams_readfs`) or an OS
   sandbox confines reads.

## Empty or missing `extensions` (2026-10-06, found by the grant-scoping review)

The user config (`~/.config/goose/config.yaml`) enables `developer`. The stub asks for `shell`
(`touch PROBE_WRITTEN`).

| Recipe `extensions` | Tools offered | `shell` |
|---|---|---|
| bare `extensions:` (null) | everything: `shell`, `write`, `edit`, `tree`, `analyze`, `delegate`, `load`, `apps__*`, `extensionmanager__*`, `todo__*`, `load_skill` | **executed** |
| key absent | same as bare | **executed** |
| `extensions: []` | none | refused |
| only `analyze` with `[__none__]` | none | refused |

7. **A recipe granted nothing must say `extensions: []`.** A null or missing `extensions` makes Goose load
   the user's configured extensions plus its defaults. That is the opposite of fail-closed. The grant-mode
   emitter writes `extensions: []` when nothing is granted.

## Delegated children (2026-10-12, after biographies pilot 1 G1/G2/G9)

`scripts/goose-probe/run_delegate_case.sh`: the prime recipe calls `delegate` (`source: child`); the child's
own recipe declares its own `extensions:`. Prime: `developer` `[write, edit, shell, tree]`, `analyze`
`[__none__]`, `summon` `[delegate, load]`, stdio `probe` (allowlist per case). Goose 1.37.0's `delegate`
schema documents `extensions` as "Extensions to enable. Omit to inherit all, empty array for none". A
`sub_recipes` entry has five fields (name, path, values, sequential_when_repeated, description) and no
extensions field.

| Case | Child recipe declares | `delegate` `extensions` | Child offered | Child's call | Result |
|---|---|---|---|---|---|
| D1 | `[]` | omitted | the prime's set minus `delegate`: `write`, `edit`, `shell`, `tree`, `load`, `probe__read_probe` | `shell touch` | **executed**: `PROBE_WRITTEN` (G1) |
| D2 | `[]` | `["probe"]` | `probe__read_probe` | `shell` | refused (G9) |
| D3 | `probe`, no allowlist | `["probe"]` (prime: `[read_probe]`) | `probe__read_probe` | `write_probe` | refused: the prime's allowlist applies |
| D3c | `probe` `[read_probe]` | `["probe"]` (prime: unrestricted) | `probe__read_probe`, `probe__write_probe` | `write_probe` | **executed**: the child's narrower allowlist is ignored |
| D3b | `[]` | `["developer"]` | `write`, `edit`, `shell`, `tree` | `shell touch` | **executed**: a named extension brings the prime's whole allowlist |
| D4 | `probe` | `["probe"]` (prime lacks it) | none | `read_probe` | not offered: `delegate` cannot add an extension (G2) |
| D5 | `[]` | `[]` | none | `shell` | not offered |
| D6 | `[]` | `["summon"]` | `load` | `delegate` | refused: a child never gets `delegate` |
| D6b | `[]` | `["summon"]`, and omitted (prime's `summon` unrestricted) | `load` | `delegate` | refused both ways |
| L1 | (direct run) `summon` `[load]` | n/a | `load` | `delegate` | refused: the allowlist withholds `delegate` |

End to end, on a generated grant-mode team (`examples/software-project` brief with `goose_tool_scoping:
"grant"`), with the generated `orchestrator.yaml` as the prime and `source: quality_auditor`:

| Case | `extensions` | Child offered | Child's call | Result |
|---|---|---|---|---|
| E1 | omitted | the orchestrator's set: readfs, `write`, `edit`, `shell`, `tree`, `load` | `write` | **executed** (G1 reproduced) |
| E2 | `["agentteams_readfs"]` (the orchestrator's table) | the five readfs tools | `write` | refused |
| E3 | `["agentteams_readfs"]` | the five readfs tools | `list_dir .` | executed |

8. **Under `delegate`, a recipe's own extensions do not apply.** The child gets the delegating session's
   extensions, narrowed only by the call's `extensions` argument, by name, with the delegator's allowlists.
   Nothing in the recipe or `sub_recipes` can narrow it, and nothing can add an extension the delegator lacks.
   Grant mode therefore gives the orchestrator the argument per target, and routes work that needs a grant the
   argument can't carry (a partial `developer`, `agentteams_runner`, an MCP server) to a direct
   `goose run --recipe` (`goose_tool_scoping.delegate_extensions`). The narrowing depends on the orchestrator
   passing the argument; Goose doesn't enforce it.

## Not covered

- A real model following the orchestrator's narrowing table (the stub calls `delegate` as told).
- A plain `goose session` that adopts the orchestrator through `.goosehints`: its extensions are the user's
  configuration, not the orchestrator recipe's, so the table's names may be missing (dropped) or unrestricted.
- Desktop and `goose session` entry points.
- Goose versions after 1.37.0: re-run `run_case.sh` and bump the pin.
