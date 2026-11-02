# WASM agent runtime — format expert reference

The `wasm` framework is agentteams' own runtime (plan P08), so its format has no upstream documentation to watch
(`FORMAT_SPECS["wasm"].upstream = False`). The format is defined in code:

- `agentteams/wasm_agent/team.py` — the rendered layout, front-matter keys, `grants.json` / `runtime.json` shapes,
  the control-plane lock and the strict loader.
- `agentteams/frameworks/wasm.py`, `_wasm_tool_scoping.py`, `_wasm_team_docs.py` — the renderer.
- `agentteams/wasm_agent/tools.py` — the tool vocabulary (`TOOL_SPEC_SHA256`, also recorded in the lock).

User guide: `docs_src/wasm-agent-runtime.md`. A format change bumps `team.FILE_VERSION` / `team.LOCK_VERSION`, so an
older team refuses to load until it is re-rendered.
