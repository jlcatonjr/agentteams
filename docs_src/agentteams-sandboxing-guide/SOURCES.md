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
| Manifest carries the request | `agentteams/analyze.py` `build_manifest`; `schemas/team-manifest.schema.json`, `schemas/project-description.schema.json` |

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
| Fail-open default; fail-closed flip under confined/exclusive | `agentteams/templates/universal/hooks/constitutional-gate.py:205` `_FAIL_CLOSED_ON_ERROR = False`; `agentteams/frameworks/claude.py` `_apply_fail_closed_policy` |

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
| Launcher macOS (`build_macos`) branch UNVERIFIED until `mac-escape-tests.sh` passes; native macOS Seatbelt + claude Linux product-arm also unverified | `agentteams/templates/universal/sandbox/confine-run.sh` (status header); `agentteams/templates/universal/sandbox/mac-escape-tests.sh`; `docs_src/api-reference/workspace-privilege-scoping.md` (macOS augmentation) |
| T6/host-as-TCB bounded; seccomp/Landlock not yet added | `agentteams/templates/universal/sandbox/confine-run.sh` (policy header) |
| Hook uncovered surfaces are the operator's responsibility | `agentteams/templates/universal/security.template.md` (delete-gate limits) |

## Related, authoritative

- [`workspace-privilege-scoping`](../api-reference/workspace-privilege-scoping.md) — the API reference
  for `privilege_profile`, the end-to-end launcher runbook, and the Linux verification verdict.
- [`host-features`](../api-reference/host-features.md) — the `*:sandbox` token + gating mechanism.
- [Security Guide, Part VI](../agentteams-security-guide/reference/part-vi-os-confinement.md) — this
  subsystem's place in the whole security stack.
