# `gate_policy` — AgentTeamsModule

The project verdict policy for the constitutional gate (#456). It lets a project turn chosen `ask` classes into `deny`.

The gate splits its verdicts:
- **`deny`** for deterministic content findings;
- **`ask`** for the Bash commands that match a Mandatory Review Trigger.

A headless or auto-approved session answers no prompt, so there an `ask` enforces nothing. A project can therefore
list the trigger classes that must be denied outright.

> *Source: `agentteams/gate_policy.py`; the format is owned by `agentteams/templates/universal/hooks/constitutional-gate.py`*

---

## The policy file

`.agentteams/gate-policy.json`, in the project root:

```json
{"version": 1, "preset": "strict", "deny": ["fs-delete"], "fail_closed_tools": true}
```

| Key | Meaning |
|-----|---------|
| `version` | Required; must be `1`. |
| `preset` | Optional; a preset defined in the gate (`strict`). The project file cannot define one. |
| `deny` | Optional; class ids to deny instead of asking. |
| `fail_closed_tools` | Optional; when `true`, a gate crash or a missing interpreter denies every tool that is not read-only, whatever the `privilege_profile`. |

The policy can only tighten:
- there is no `allow`, and no way to turn `deny` into `ask`;
- any other key, unknown class, unknown preset or wrong type is a load error.

The gate exports its enums:
- `ASK_CLASSES`;
- `POLICY_ONLY_CLASSES`;
- `GATE_POLICY_PRESETS`.

### Class ids

The default `ask` classes:
- `elevated-privilege`, `remote-pipe-shell`, `persistence`, `package-install`, `git-history-destructive`, `fleet-lock-lift`;
- `outbound-upload`, `outbound-derived`, `raw-socket`, `dns-exfil`, `publication-channel`, `interpreter-send`;
- `gh-delete`, `pr-merge`, `git-remote-delete`, `git-ref-delete`, `agentteams-branch-delete`;
- `fs-delete`, `infra-delete`, `db-delete`, `interpreter-delete`;
- `command-too-long`, `git-config-write`, `scanner-unverified`, `no-interpreter`.

Policy-only refinements are evaluated only when a policy denies them, so a project without a policy never sees them:
- `rm-recursive-force`;
- `git-force-push-short` (`git push -f`, or a `+refspec`);
- `git-alias-config` (`git -c` or `git config` for `alias.`, `push.`, `remote.` or `url.`).

`strict` denies these classes. They are the downstream wrapper's list from issue #456, plus `command-too-long`:
- `git-history-destructive`, `git-force-push-short`, `git-remote-delete`, `git-alias-config`;
- `publication-channel`, `rm-recursive-force`, `remote-pipe-shell`, `db-delete`;
- `command-too-long`, so padding cannot hide a denied class. Under any non-empty policy an over-long command is
  denied anyway.

With a policy, the gate checks every class a command matches, not only the first. So `sudo rm a` is denied when
`fs-delete` is denied.

## Pinning

At render, `agentteams` validates the file (an invalid file stops the render) and writes its SHA-256 into the
installed gate (`_GATE_POLICY_SHA256`). This is the same emit-time mechanism as `_FAIL_CLOSED_ON_ERROR`, so the
template's own pin moves once, for this change.

The gate reads the file under its own project (the hook's `parents[2]`, never the working directory or
`CLAUDE_PROJECT_DIR`). It reads it once, without following a symlink, then hashes and parses those same bytes:

| Recorded digest | File | Verdicts |
|-----------------|------|----------|
| none | any | Today's defaults; an unrecorded file is ignored, and `--check-wiring` says so. |
| set | matches and valid | The policy applies. |
| set | missing, altered, a symlink, or invalid | `deny` (`policy-unverified`) for every tool that is not read-only. |

Once a policy is recorded, three more protections apply:
- The gate denies `Write`/`Edit` aimed at the policy file, at itself, or at its interpreter pin (`policy-protected`).
  It also denies a Bash line that names one of them literally (case-insensitive) together with a redirect or any
  of a broad list of commands: `git`, `sed`, `python`, `tar`, `cp`, `mv` and others. This is deliberately coarse,
  so even `git diff` or `sed -n` on the gate is denied while a policy is recorded. Read them with the Read tool.
- The Claude sandbox block adds `denyWrite` and `Edit` rules for the same three files
  (`_control_plane_protect.gate_policy_protect_paths`).
- Recording a **weaker** policy (fewer denied classes, `fail_closed_tools` dropped, or the file removed) is a Rule 15
  relaxation, `gate-policy-weaken:<framework>`. A governed workspace needs the operator's Ed25519-signed decision
  for it; elsewhere it gets the SANDBOX RELAXATION notice.

## Limits

- **The pin is only as strong as the write protection on the gate.** On Copilot, Grok, and any team whose OS sandbox
  is off, it is advisory, and `--check-wiring` says so.
- **The hook's matcher limits the deny.** With `Write|Edit|NotebookEdit|MultiEdit|Bash`, a failed policy denies those
  tools; tools outside the matcher never reach the gate.
- **The OS denies cover files, not directories.** Whether Seatbelt or bwrap also blocks renaming
  `.claude/hooks/` or `.agentteams/` around a denied file has not been verified (@security F6, remediation row 700).
  In the meantime, the gate's own `policy-protected` check denies Bash lines that name these files with a write verb.
- **The harness's own fail-open is unchanged.** A harness timeout, or a hook that is removed from settings, still lets
  the call through; `fail_closed_tools` covers only the hook's own failures.

---

## Functions

| Function | Purpose |
|----------|---------|
| `project_policy(project_root)` | The validated `RecordedPolicy`, or None; raises `GatePolicyRenderError` for an invalid or symlinked file. |
| `manifest_policy(manifest)` | `project_policy` for the manifest's CLI project root. |
| `policy_from_bytes(raw)` | Validate bytes with the gate's own `parse_gate_policy`. |
| `apply_gate_policy(hook_text, policy)` | Write the digest into the emitted gate (unchanged without a policy). |
| `recorded_digest(hook_text)` | The digest an installed gate records: `""` for none, None when the line is malformed. |
| `weakens(old, new)` | Whether `new` is weaker than `old` (Rule 15). |
| `wiring_lines(project_root, gate_path, advisory=...)` | The `--check-wiring` report. |
| `gate_module()` | The gate template loaded as a module (never run as `__main__`). |
