# Goose tool-scoping probe

Re-verifies the Goose facts that `agentteams/frameworks/goose_tool_scoping.py` relies on. Run it before
bumping `GOOSE_MAP_VERSION`. Results for Goose 1.37.0 are in `references/goose-tool-scoping-spike.md`.

- `stub_model.py`: a stub of the OpenAI chat API. It logs the tools Goose offers, requests one named
  call, then ends the turn.
- `probe_mcp.py`: a stdio MCP server with `read_probe` and `write_probe` (which creates `PROBE_WRITTEN`).
- `run_case.sh NAME PORT TOOL ARGS_JSON EXTENSIONS_YAML`: runs `goose run --recipe` against the stub,
  with an isolated `HOME` and `GOOSE_MODE=auto`. It prints the offered tools and the call result, and
  reports whether the work directory changed.

Needs `goose` on `PATH`. It makes no network calls; the model is the local stub.
