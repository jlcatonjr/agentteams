# `sandbox_state`

The effective sandbox state of a render, its build-log record, and the previous state (WASM-sandbox program,
METAPLAN §2.1). Every consumer that asks "is an OS boundary emitted?" reads this module, never
`privilege_profile` directly. P15 owns the dataclass and the record; P00 derives the OS half; P01 extends it
for WASM (until then the WASM fields stay at their off defaults). The fuller guide is written by P18.

## Public surface — the OS switch (P00)

```python
OS_SANDBOX_VALUES = ("on", "off")
OS_SANDBOX_CLI_KEY = "_os_sandbox_cli"                 # transient: --os-sandbox (never from input)
EXPLICIT_SANDBOX_TOKENS_KEY = "_explicit_sandbox_tokens"  # transient: explicit argv *:sandbox tokens

validate_os_sandbox(value: object) -> str | None
effective_sandbox_state(manifest: dict) -> SandboxState
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
P15 gate (`cli/sandbox_relaxation_gate`) whenever the switch is set or the previous render recorded a
`sandbox_state`, and records `manifest["sandbox_state"]` for the build-log.

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
on-disk boundary evidence can only turn a switch on.

## See also

- [`host_features`](host-features.md) — `merge_profile_features(..., os_sandbox=)`.
- [`--os-sandbox`](../cli-reference.md) and the brief key `os_sandbox` (`DESCRIPTION-FORMAT.md`).
