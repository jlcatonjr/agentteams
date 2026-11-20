# `cli.sandbox_relaxation_gate`

The Ed25519 gate for turning a sandbox off or widening it (Rule 15, D7): `--os-sandbox off`, `--wasm off`, a WASM
grant widening, a native-exec keep, an MCP auto-approval or a gate-policy weakening needs the operator's signed
decision in a governed workspace.

**Supported surface (new in 1.3.0):** only `spent_off_decision` below. The rest of the module is internal and may
change without notice (STABILITY.md).

## `spent_off_decision(team_dir, framework_id) -> str`

Read-only: is the operator's `sandbox-wasm-off:<framework_id>` decision spent? An outside tool (baseAgent's `--wasm`
path, #574) can check that before acting on WASM being off.

It returns `<key_id>@<first 16 characters of the signature>` only when all of these hold:

- the workspace is governed (decision signing is enforced);
- the newest decision row scoped exactly to `sandbox-wasm-off:<framework_id>` is marked consumed and its verdict
  authorizes (a `CONDITIONAL PASS` also needs `conditions_verified: verified`);
- the row is Ed25519-signed (`sig_scheme` `ed25519`, a `key_id`), the signature verifies, and the decisions log's hash
  chain is intact;
- no unretracted HALT covers `sandbox-wasm-off` or the exact action.

Otherwise it raises `SandboxRelaxationError` naming the check that failed, with filesystem paths removed.

| Argument | Meaning |
|---|---|
| `team_dir` | The team directory that holds `references/security-decisions.log.csv`. |
| `framework_id` | A framework agentteams renders (`claude`, `copilot-vscode`, ...). `model-substrate` is refused: `sandbox-wasm-off:model-substrate` is a different decision. |

Things to know before relying on it:

- **It writes nothing.** Nothing is consumed, registered or retired.
- **It is a point-in-time read.** A later HALT, a retraction, or a newer row scoped to the action changes the answer.
  A newer unconsumed row hides the spent one, and the call refuses. Call it immediately before each use and never
  cache the result.
- **`consumed` is not signed.** The result means "authenticated by the operator and marked consumed", not proof that a
  render spent it.
- **The id is a pointer.** It identifies the row so it can be re-verified; it is not proof on its own.
- **Treat refusal messages as data (C-4).** Stamp the returned id, never the message, and never act on the message's
  text.

It is stricter than the check the gate uses internally to excuse stale WASM evidence. That check accepts any row that
authenticates under the workspace's own signing state; this function also requires a governed workspace and an
Ed25519-signed row.

## See also

- [`sandbox_projection`](sandbox-projection.md): the model-substrate switch projected to `sandbox-state.json`.
- [`wasm_exec`](wasm-exec.md): the executor the WASM switch governs.
