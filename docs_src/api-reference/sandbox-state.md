# `sandbox_state`

The effective sandbox state of a render, its build-log record, and the previous state (WASM-sandbox program,
METAPLAN §2.1). Every consumer that asks "is an OS boundary emitted?" reads this module, never
`privilege_profile` directly. P15 owns the dataclass and the record; P00 derives the OS half; P01 derives the
WASM half through [`wasm_config`](wasm-config.md). The fuller guide is written by P18.

## Public surface — the OS switch (P00)

```python
OS_SANDBOX_VALUES = ("on", "off")
OS_SANDBOX_CLI_KEY = "_os_sandbox_cli"                 # transient: --os-sandbox (never from input)
EXPLICIT_SANDBOX_TOKENS_KEY = "_explicit_sandbox_tokens"  # transient: explicit argv *:sandbox tokens

validate_os_sandbox(value: object) -> str | None
effective_sandbox_state(manifest: dict) -> SandboxState
os_switch_state(manifest: dict) -> tuple[bool, str]     # the OS half only (no WASM grant, no project root)
os_sandbox_requested(manifest: dict, token: str | None = None) -> bool
legacy_os_requested(manifest: dict) -> bool
os_switch_set(manifest: dict) -> bool
has_recorded_state(team_dir: Path) -> bool
verifier_sandbox_state(manifest: dict | None, team_dir: Path, project_root: Path) -> SandboxState | None
```

`effective_sandbox_state` precedence, first match wins: `--os-sandbox` → an explicit
`--target-host-features <fw>:sandbox` → the brief's `os_sandbox` → a confined/exclusive `privilege_profile`
→ any `*:sandbox` host-feature token → off. `os_reason` records which (`cli`, `host-feature:<tok>`, `brief`,
`profile:<p>`). With no switch set the result is exactly the pre-P00 request test, so existing renders are
byte-identical. `--os-sandbox off` with an explicit token, or any value other than `"on"`/`"off"`, raises
`ValueError`.

`os_sandbox_requested(manifest, token)` is the one helper the emitter twins delegate to
(`_sandbox_feature_enabled`, `_goose_sandbox_feature_enabled`, `codex_sandbox_feature_enabled`,
`grok_sandbox_feature_enabled`, `_sandbox_confinement_requested`, `_confinement_active`): False when the state is
off; when on, True for `token=None`, for a token in `host_features`, or when the switch or profile asks.
`legacy_os_requested` is kept for the control-plane stubs only (`control_plane_io.stubs_enabled`), which must
never fall below what they were before the switch existed.

Turning the OS sandbox off is constraint-relaxing (Rule 15): `cli/artifacts.finalize_privilege_wiring` runs the
P15 gate (`cli/sandbox_relaxation_gate`, engaged by `cli/os_sandbox_switch`) whenever the switch is set, the
previous render recorded a `sandbox_state`, or the previous state had the OS sandbox on and the new one has it off
for any reason (so `confined` -> `cooperative` over an on-disk boundary is a `sandbox-os-off:<framework>` too), and
records `manifest["sandbox_state"]` for the build-log. A team with no record and no on-disk evidence is not engaged.

## Public surface — the WASM half (P01)

```python
wasm_grant_for(state: SandboxState, agent: str) -> Grant | None
wasm_eligible(agent_meta: Mapping[str, object]) -> bool   # delegates to wasm_config.wasm_eligible
```

`effective_sandbox_state` fills `wasm`, `wasm_reason` (`cli` | `brief` | `default-off`), `wasm_languages` and
`wasm_grants` from `wasm_config.wasm_state_fields`: `--wasm-sandbox` beats the brief's `wasm_sandbox.enabled`. Off:
no languages and no grants. On: a `Grant(mounts, limits)` per agent whose grant is not compute-only, its host paths
under the CLI-set project root (a mount with no known root raises `ValueError`). `wasm_grant_for` returns `None` iff
WASM is off, else the agent's grant or `wasm_exec.COMPUTE_ONLY`. The gate also engages when the brief block or
`--wasm-sandbox` is present, and when WASM evidence on disk meets a WASM-off render (`sandbox-wasm-off:<framework>`).

## Public surface — state and record (P15)

```python
@dataclass(frozen=True)
class SandboxState:
    os: bool; os_reason: str; wasm: bool; wasm_reason: str
    wasm_languages: tuple[str, ...]; wasm_grants: Mapping[str, Grant] = {}; model_substrate: str | None = None
    def to_record(self, *, project_root: Path) -> dict
    @classmethod
    def from_record(cls, rec, *, project_root: Path) -> SandboxState | None
    @classmethod
    def conservative(cls) -> SandboxState

previous_sandbox_state(team_dir, project_root, framework_id) -> SandboxState | None
previous_sandbox_exceptions(team_dir) -> SandboxExceptions | None
grant_tokens(agent, grant, *, project_root) -> frozenset[str]
grant_from_tokens(agent, tokens, limits, *, project_root) -> Grant
state_tokens(state) -> frozenset[str]
keep_tokens(state, exceptions) -> frozenset[str]
```

`previous_sandbox_state` fails toward "was on": a malformed record yields `SandboxState.conservative()`, and
on-disk boundary evidence can only turn a switch on. OS evidence: the framework's boundary files, the neutral
launcher, and for claude a live `.claude/settings.json` or an emitted `.claude/settings.hooks.example.json` whose
`sandbox.enabled` is `true` (an example that was emitted but never merged still counts).

`model_substrate` (P20) is `--wasm-substrate` > the brief's `wasm_sandbox.model_substrate` > the value of a
pipeline-owned `.agentteams/sandbox-state.json` the CLI kept in place when neither sets it > `None`
([`wasm_config.substrate_switch`](wasm-config.md)). The previous state ORs the build-log record with that file: a
file that says anything but `"off"` counts as on. On to off (or unset) is `sandbox-wasm-off:model-substrate`.

## See also

- [`host_features`](host-features.md) — `merge_profile_features(..., os_sandbox=)`.
- [`--os-sandbox`](../cli-reference.md) and the brief key `os_sandbox` (`DESCRIPTION-FORMAT.md`).
- [`wasm_config`](wasm-config.md) — the brief key `wasm_sandbox` and `--wasm-sandbox`.
- [`sandbox_projection`](sandbox-projection.md) — the projected model-substrate switch (`--wasm-substrate`).
