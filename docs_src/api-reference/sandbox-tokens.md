# `sandbox_tokens`

The WASM grant-token spelling and the guest read-path grammar (constraint-relaxing-authorizations rule, D7). A stdlib-only leaf module: it imports
nothing from `agentteams`, so `sandbox_state` (the one grant builder) and `wasm_agent.team` (the `--framework wasm`
loader) build tokens without importing the CLI package. `cli/sandbox_effects` re-exports these functions as part of
the relaxation vocabulary it shares with the effect classifier and the render-time gate. Integrity-pinned.

## Public surface

```python
checked_slug(value, what) -> str          # ^[a-z0-9][a-z0-9-]*$, else ValueError naming `what`
valid_rel(rel) -> str                     # segments [A-Za-z0-9._-]+, no '.' / '..' / empty segment
read_token(agent, rel) -> str             # "wasm:<agent>:read:<rel>"
scratch_token(agent) -> str               # "wasm:<agent>:scratch"
native_exec_token(agent) -> str           # "native-exec:<agent>"
```

Each raises `ValueError` for a bad slug or path.
