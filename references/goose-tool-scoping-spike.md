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

## Not covered

- Delegation and subagent inheritance (mathAgents tested `delegate`).
- Desktop and `goose session` entry points.
- Goose versions after 1.37.0: re-run `run_case.sh` and bump the pin.
