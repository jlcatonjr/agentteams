# Sources — every fact in the sandboxing guide, mapped to code

> Every canonical fact in [`SKELETON.md`](SKELETON.md) rests on a repo source. This file collects them
> so a reviewer can check any claim against the implementation. Line numbers are indicative (they drift
> with edits); the symbol names are the stable anchors.

## Request & profiles (SB4–SB6)

| Fact | Source |
|---|---|
| Three profiles; unknown fails closed | `agentteams/host_features.py` — `validate_privilege_profile` |
| Profile → token expansion (goose→goose:sandbox, else claude:sandbox) | `agentteams/host_features.py` — `expand_privilege_profile`, `merge_profile_features` |
| `:sandbox` rejected for a namespace with no emitter | `agentteams/host_features.py` — `validate`, `_KNOWN_FEATURES` |
| Confinement requested = profile OR token, on convert/render too | `agentteams/frameworks/_sandbox_emit.py:116` `_sandbox_feature_enabled`; `agentteams/frameworks/_linux_sandbox_emit.py` `_sandbox_confinement_requested` |
| Read-exclusion set (exclusive) | `agentteams/frameworks/_sandbox_emit.py` `_exclusive_read_deny_paths`, `_DEFAULT_PROTECTED_READ_PATHS` |
| Manifest carries the request | `agentteams/analyze.py` `build_manifest`; `agentteams/schemas/team-manifest.schema.json`, `agentteams/schemas/project-description.schema.json` |

## The decision (SB7–SB9)

| Fact | Source |
|---|---|
| Capability matrix (POSIX framework-neutral: any framework on Linux AND macOS; claude everywhere; Windows none) | `agentteams/host_features.py:222` `is_sandbox_capable` (linux + darwin True) |
| Three advisory codes (1 fatal, 2 non-fatal) | `agentteams/host_features.py:325` `privilege_profile_advisory`; `:376` `privilege-profile-unenforced-host` (fatal); `:404` `privilege-profile-linux-launcher-manual-wire`; `:426` `privilege-profile-macos-launcher-manual-wire` |
| Fail-closed only on the fatal code | `agentteams/cli/artifacts.py:330` `resolve_host_features_and_advise`; `:381` `fatal = advisory["code"] == "privilege-profile-unenforced-host"` |
| `--allow-unenforced-confinement` opt-out | `agentteams/cli/parser.py` (flag); `agentteams/cli/artifacts.py` (raise vs warn) |

## The mechanisms (SB10–SB13)

| Fact | Source |
|---|---|
| Claude settings-block sandbox (allowWrite/denyRead/denyWrite/allowUnsandboxedCommands) | `agentteams/frameworks/_sandbox_emit.py:176` `_build_sandbox_block`; `agentteams/frameworks/claude.py:249` (gate) |
| Goose macOS Seatbelt profile + inert config example; darwin guard | `agentteams/frameworks/_goose_sandbox_emit.py:333` `goose_sandbox_output_files`, `:350` (darwin guard); `_seatbelt_path_expr` |
| Dual-OS launcher: emit paths + flags (Linux `bwrap` + macOS `build_macos`) | `agentteams/frameworks/_linux_sandbox_emit.py:112` `linux_sandbox_output_files`, `:154` `macos_sandbox_output_files`; `agentteams/frameworks/base.py` `extra_output_files` (linux + macos); `agentteams/templates/universal/sandbox/confine-run.sh` |
| Framework-neutral wiring + rel-path depth; no double-emit | `agentteams/frameworks/base.py:125` `sandbox_launcher_rel_path`, `:139` `extra_output_files`; `codex.py`/`agents_md.py` overrides; `claude.py`/`goose.py` `super()` |
| Emitted launcher made executable | `agentteams/atomicio.py` (`#!`→+x); `agentteams/convert.py` |

## Wiring & runtime enforcement (SB14–SB17)

| Fact | Source |
|---|---|
| Ship-an-example, never write live config | `agentteams/frameworks/hooks_emit.py`; `agentteams/frameworks/claude.py` (settings example) |
| Wiring verifiers (claude; goose, platform-honest) | `agentteams/frameworks/claude.py:320` `verify_sandbox_wiring`; `agentteams/frameworks/_goose_sandbox_emit.py:392` `verify_goose_sandbox_wiring` |
| PreToolUse delete-authorization gate; scope + limits | `agentteams/templates/universal/hooks/constitutional-gate.py:63`; `agentteams/templates/universal/security.template.md` |
| PR merges route to the operator: `gh [flags] pr [flags] merge`, REST `pulls/<n>/merge`, GraphQL `mergePullRequest`/`enablePullRequestAutoMerge`, any client (PR #173) | `agentteams/templates/universal/hooks/constitutional-gate.py:75-85`; `tests/test_constitutional_gate_hook.py:280-292` (gh, curl, wget, httpie, python), `:226-228` (benign non-merges) |
| The match is over one `Bash` command string, case-insensitive (so obfuscation/splitting evades it) | `agentteams/templates/universal/hooks/constitutional-gate.py:157-161` |
| Fail-open default; fail-closed flip under confined/exclusive | `agentteams/templates/universal/hooks/constitutional-gate.py:230` `_FAIL_CLOSED_ON_ERROR = False`; `agentteams/frameworks/claude.py` `_apply_fail_closed_policy` |

## Write policy & the `agentteams_runner` MCP server (SB24)

Line numbers are as of `origin/main` at `3399974` (PR #174).

| Fact | Source |
|---|---|
| Opt-in switch; a default team renders unchanged | `agentteams/write_policy.py:164` `enabled`, `:309-339` `apply` |
| One resolver for native generation and interop; refusals (framework, `cooperative`, non-explicit profile, legacy Goose scoping); `write_policy_frameworks` scoping; codex only via its launcher on Linux/macOS | `agentteams/write_policy.py:179-240` `resolve`; `agentteams/analyze.py:282` |
| Narrowing to `read`/`search`/`todo`, `read` always kept; legacy `allowed-tools:` removed; orchestrator slugs exempt | `agentteams/write_policy.py:29-40`, `:263-307` `narrow_tools` |
| The fenced sections (proposals, runner, orchestrator) | `agentteams/write_policy.py:49`, `:73`, `:84`, `:109`, `:341-347` `_section` |
| Runner model: out-of-session, sole key holder, confined commands/gates, refuse without a usable sandbox unless `allow_unconfined_runs` | `agentteams/proposal_runner.py:1-19` |
| Key from a 0600 file under `~/.config/agentteams/keys/`; refuse a key in the environment | `agentteams/proposals.py:109-115`; `agentteams/proposal_runner.py:276-282` |
| Runner refuses a brief without the switch; pins brief, confined file, grants file | `agentteams/proposal_runner.py:268-271`, `:504-521` |
| Channel from directory; MCP kinds; serial queue; 120 s MCP command cap | `agentteams/proposal_runner.py:43-73` |
| Installed-server SHA256 check on every poll | `agentteams/proposal_runner.py:190-237`, `:519-521` |
| Staged → direct only on a verified grant; deletes never upgrade | `agentteams/proposal_runner.py:402-433`; `agentteams/proposal_staging.py:101-128` `apply_direct` |
| Staging store, TTL, approval re-runs checks | `agentteams/proposal_staging.py:1-14`, `:31-33`, `:166` `apply_staged` |
| Five tools; install path; `-I -S`; pinned SHA256; system interpreters | `agentteams/runner_mcp.py:21-27`, `:67` |
| Server is a keyless queue client, not a trust boundary; root from install location | `agentteams/data/agentteams-runner-mcp.py:14-24`, `:357-367` |
| Deny list on requested AND resolved path | `agentteams/data/agentteams-runner-mcp.py:56-62`, `:261-269` |
| Generated agents launch the server `staged` | `agentteams/frameworks/claude.py:151-155`; `agentteams/frameworks/goose.py:280-286` |
| `mcp_grants` validation; unsigned `direct` acts as staged | `agentteams/proposal_policy.py:220-245` |
| Ed25519 operator grants (a Rule 15 constraint-relaxing change): binding, ≤30 days, ≤500 writes, ≤3 active, operator-owned file | `agentteams/mcp_direct_grants.py:1-18`, `:37-48`, `:174-221` `verify`, `:224-259` `active_grants` |
| `AR_WRITE_POLICY` shares the generator's token set | `agentteams/audit_agent_contract.py:33-34`, `:778` `_check_write_policy` |
| `--check-wiring` runs `runner_mcp.wiring_problems` (configuration checks) | `agentteams/cli/standalone_modes.py:204-261`; `agentteams/runner_mcp.py:171-296` |
| Interop carries the policy (claude/goose only) and installs the server (PR #174) | `agentteams/interop_write_policy.py:1-55`, `:193` `install_server`; `agentteams/cli/app.py:494` |
| Layer modules integrity-pinned | `agentteams/integrity.py:54-68` |
| Server tested as a subprocess, not via a live harness | `tests/test_runner_mcp.py` (`served` fixture + `Client`) |

## Integrity, provenance & drift (SB18–SB19)

| Fact | Source |
|---|---|
| Pins the emitters AND the launcher asset | `agentteams/integrity.py:65` (`_sandbox_emit.py`), `:71` (`_linux_sandbox_emit.py`), `:77` (`confine-run.sh`); `references/enforcement-integrity.json` |
| Regenerate deliberately; the diff is the control; probe E4 | `agentteams/integrity.py` `write_manifest`, `verify`; `tests/test_redteam_integrity_coverage.py` |
| Verbatim emission + consumer-neutral header + sha-pin drift | `agentteams/frameworks/_linux_sandbox_emit.py`; `agentteams/templates/universal/sandbox/confine-run.sh` (provenance header) |

## Honest ceilings & red-team (SB20–SB21)

| Fact | Source |
|---|---|
| Launcher Linux (`bwrap`) branch enforcement-VERIFIED (live-kernel deny test) | `agentteams/templates/universal/sandbox/confine-run.sh` (status header); `docs_src/api-reference/workspace-privilege-scoping.md` |
| Launcher macOS (`build_macos`) branch UNVERIFIED until `mac-escape-tests.sh` passes; native macOS Seatbelt also unverified; claude Linux product arm verified 2026-09-30 only with the documented bwrap AppArmor profile | `agentteams/templates/universal/sandbox/confine-run.sh` (status header); `tests/test_os_sandbox_product_enforcement.py`; `agentteams/templates/universal/sandbox/mac-escape-tests.sh`; `docs_src/api-reference/workspace-privilege-scoping.md` (macOS augmentation) |
| T6/host-as-TCB bounded; seccomp/Landlock not yet added | `agentteams/templates/universal/sandbox/confine-run.sh` (policy header) |
| Hook uncovered surfaces are the operator's responsibility | `agentteams/templates/universal/security.template.md` (delete-gate limits) |
| Write-policy layer: runner/operator host is the TCB; covered frameworks | `agentteams/proposals.py:109-115`; `agentteams/mcp_direct_grants.py:1-18`; `agentteams/write_policy.py:179-240` |

## Related, authoritative

- [`workspace-privilege-scoping`](../api-reference/workspace-privilege-scoping.md) — the API reference
  for `privilege_profile`, the end-to-end launcher runbook, and the Linux verification verdict.
- [`host-features`](../api-reference/host-features.md) — the `*:sandbox` token + gating mechanism.
- [`write-policy`](../api-reference/write-policy.md), [`runner-mcp`](../api-reference/runner-mcp.md),
  [`proposal-staging`](../api-reference/proposal-staging.md),
  [`mcp-direct-grants`](../api-reference/mcp-direct-grants.md),
  [`interop-write-policy`](../api-reference/interop-write-policy.md) — the API references for SB24
  (orientation; the facts above cite the code).
- [Security Guide, Part VI](../agentteams-security-guide/reference/part-vi-os-confinement.md) — this
  subsystem's place in the whole security stack.
