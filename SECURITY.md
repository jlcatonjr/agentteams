# Security Policy

## Supported versions

| Version | Status                  |
| ------- | ----------------------- |
| 1.x     | Active support          |
| 0.x     | Unsupported (pre-1.0)   |

Security fixes are backported only to the most recent minor release of the
current major. Once 2.0 ships, 1.x will receive critical security fixes for
six months and then move to unsupported.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Email: **maintainer@example.org** with subject prefix `[agentteams-security]`.

Please include:
- A description of the vulnerability.
- Steps to reproduce, or a proof-of-concept.
- The agentteams version (`agentteams --version`) and Python version.
- Whether the issue is exploitable from CLI input, from a malicious project
  description, from a malicious git remote, or from a malicious template.

You will receive an acknowledgement within **3 business days**. A fix
target and disclosure schedule will be communicated within **10 business
days** of acknowledgement.

## Threat model

`agentteams` is a code-generation tool that runs locally and writes files
to a target project directory. The following are considered in-scope
threats:

- **Untrusted project descriptions.** A malicious `_build-description.json`
  or `agent-team.md` should not cause arbitrary file writes outside the
  target project directory, command injection, or code execution.
- **Untrusted templates.** A malicious template file should not exfiltrate
  environment variables or execute shell commands at render time.
- **Destructive flag misuse.** Flags like `--overwrite`, `--prune`, and
  `--migrate` are gated by the security-decision system documented in
  [`agentteams/security_refs.py`](agentteams/security_refs.py). Bypassing
  that gate without using the documented `--yes` interaction is a
  vulnerability.
- **Snapshot-tag handling.** `--migrate` writes a `pre-fencing-snapshot`
  git tag and `--revert-migration` restores from it. Losing or silently
  moving that tag without user consent is a vulnerability.

Out of scope:
- Vulnerabilities that require an attacker with write access to the target
  project directory **acting outside an agentteams operation** — i.e. someone
  who can already edit the generated files directly, by hand, without the
  tool's involvement.

  This exclusion is deliberately narrower than "write access to the target is
  out of scope". Surviving re-generation is a **claimed property** of `FENCED`
  regions: a fenced section is module-owned and restored from the template on
  every `--update --merge`, precisely so that on-disk tampering does not
  persist. An exclusion broad enough to cover any attacker with write access
  would make that property pointless to claim. If a fenced region does *not*
  survive a merge, that is a vulnerability.

  The property is claimed for `FENCED` regions and **only** for them.
  Everything outside a fence — including YAML front matter, which cannot be
  fenced because it must be the first bytes of the file — is preserved
  unconditionally by design, and its loss or alteration is not a merge defect.

  The realistic in-scope adversary is not a person with a text editor. It is
  an agent operating inside the target repository with legitimate write access
  and acting on injected instructions (OWASP LLM06, "Excessive Agency" —
  enumerated as a mandatory review trigger in the generated security agent).
- Vulnerabilities in third-party tools agentteams delegates to (git, the
  user's editor, downstream LLM CLIs).
- Bugs in generated agent files themselves — agentteams is a generator,
  not a runtime; generated outputs are the user's responsibility to review.

### Design-time vs runtime governance

The generated agent team governs **how an application is built** — its agents
review the code you and your AI assistants write. It does **not** run inside the
application you produce. In particular, the generated `security` agent is
read-only and HALTs at *review* time; nothing it emits executes in your shipped
app's request path.

So if your produced application **serves LLM output to end users** (a chat UI, a
tutor, an assistant), the generated team does not police that runtime — you must
add runtime governance yourself. The word "team" describes design-time build
governance, not an app that polices itself at runtime. See the
[Runtime Security for Served Apps](docs_src/runtime-security-guide.md) checklist
(output-safety gate, "data-not-instructions" for your app's own prompts, input
sanitization + bounds).

### The `agentteams[research]`/`[browser]` extras are a disclosed, bounded exception to this boundary

Everything above describes `agentteams`'s CLI/template-rendering output, which remains
design-time-only and unchanged.

**Install agentteams only from its git source.** The project is not published on PyPI, so
installing the name `agentteams` from PyPI (with or without an extra) gets whoever holds it there.
Every install instruction this project emits uses the `git+https://github.com/jlcatonjr/agentteams.git`
form. The optional `research` extra
(`pip install "agentteams[research] @ git+https://github.com/jlcatonjr/agentteams.git"`) — and its heavier `browser` sibling
(`pip install "agentteams[browser] @ git+https://github.com/jlcatonjr/agentteams.git"`, layered on top of `research`, adding real Playwright-driven
browser rendering for JavaScript-heavy pages, and requiring a further one-time
`playwright install chromium` beyond the `pip install` itself) — are a genuinely different kind of
thing: real, importable Python libraries (`agentteams.research`, `agentteams.research.browser`) a
consuming project may add as its **own** runtime dependency and call directly — the same
relationship any project has with any dependency, not agentteams reaching into a produced app's
runtime uninvited. Neither has import-time coupling to the CLI/generator pipeline in either
direction; `browser` additionally has no import-time coupling to `research`'s own package-level
exports (it is deliberately not re-exported from `agentteams.research.__init__`, so a plain
`agentteams[research]` install never risks touching Playwright). The `research-analyst`
domain-archetype template documents the recommended way to give an LLM agent instructions for
orchestrating both; see [`docs_src/api-reference/research.md`](docs_src/api-reference/research.md)
for the libraries' own documented surface and stability status, including `browser`'s two-layer
SSRF guard and its named DNS-rebinding limitation.

## Security-relevant changes in this release

See the `### security` blocks of [`CHANGELOG.md`](CHANGELOG.md). For 1.0
specifically:
- `--migrate` gate exemption is in-process only (no CLI flag).
- `--revert-migration` is intentionally ungated (it is the recovery path).
- `--migrate` no longer hard-errors on a stale snapshot tag; with `--yes`
  it moves the tag to current HEAD.
- `--sync-agent-docs` writes agent files from outside every sandbox. It moves only the
  `AGENTTEAMS-LEARNED` block, scan- and policy-gates it, and never creates files. It writes
  `.claude/agents` only with `--apply --include-claude`, which needs an interactive y/N at a
  terminal and is refused when `CLAUDECODE` is set. Both of its modules are integrity-pinned.
  "Concurrent edit wins" is best effort: there is a small window between the re-check and the
  rename.

## Signing from a trusted install

The operator's Ed25519 private key is read in the process that runs `--sign-decision` and `--sign-grant`.
Code an agent can edit must therefore never run that process. The minters check this and refuse by
default (#10); every in-process check is a speed bump only, because tampered code can skip it.

1. Clone a release tag into a directory outside every agent write root.
2. Verify the tag with repository config neutralised:
   ```
   git -c gpg.format=ssh -c gpg.ssh.program=/usr/bin/ssh-keygen \
       -c gpg.ssh.allowedSignersFile=$HOME/.config/agentteams/allowed_signers \
       -c gpg.program=/usr/bin/gpg -c core.fsmonitor=false -c core.hooksPath=/dev/null \
       tag -v <tag>
   ```
   Keep `allowed_signers` outside every write root. Release tags are signed from rc8 onward; until then,
   pin a tag you have reviewed.
3. Install from that clone with `pipx install "./<clone>[signing]"`, with `PIPX_HOME` outside the project.
   Do not use a `git+https@tag` URL: it fetches the code again.
4. Run the absolute console-script path. Never run `python -m agentteams` from a project root, because it
   imports the project's checkout.

Run the signer from a directory outside the install. With the current directory at `$HOME`, a pipx
install under `~/.local` counts as "inside the current directory" and is refused. `--allow-checkout-signing`
is recorded in signed **decisions** only. Grant rows have no free-text signed field, so a grant signed with it
carries no marker; a signed marker is planned for the next grant schema version.

`agentteams --sign-*` also prints install-location warnings:
- the package dir is owned by someone other than you or root, or is group- or world-writable;
- the dist-info `RECORD` hashes differ (`RECORD` lives beside the files it hashes, so this is a speed bump
  too);
- a `.pth` file runs `import` lines;
- the current directory is on `sys.path`.

## Advisories

### 2026-10-01 — Claude Code sandbox.excludedCommands is not a safe boundary (2.1.251)

**Affected:** any project whose live `.claude/settings.json` sets `sandbox.excludedCommands`,
measured on Claude Code 2.1.251 (Linux). agentteams has never emitted the key. **Severity:** high
for anyone who adds it: it is an escape from the sandbox, not a narrow exception.

Measured with `sandbox.excludedCommands = ["/bin/bash /abs/fixed.sh"]` and
`allowUnsandboxedCommands: false`. The script itself was write-denied, and paths are sanitized:

| Command run by the agent | Result |
|---|---|
| `/bin/bash /abs/fixed.sh` (exact) | runs **unsandboxed** (intended) |
| `PROBE_ENV=1 /bin/bash /abs/fixed.sh` | runs **unsandboxed, with the variable set** |
| `cd X && /bin/bash /abs/fixed.sh` | runs **unsandboxed** |
| `/bin/bash /abs/fixed.sh extra-arg` | sandboxed |
| `env VAR=1 /bin/bash /abs/fixed.sh` | sandboxed |
| `/bin/bash /abs/fixed.sh; touch /tmp/evil; echo >> .claude/agents/x` | the chained writes are **blocked** (each subcommand must match) |

So a leading environment assignment survives the exclusion match. Put a variable such as
`LD_PRELOAD=/proj/x.so` or `PYTHONPATH=/proj/lib` in front of the excluded command, and an agent
runs code it wrote **outside** the sandbox, whatever the excluded script does. Only the
`PROBE_ENV` form was measured; these variables follow from it. Write-protecting the script
does not help.

**agentteams' response:**
- The key is never emitted. A pinned test checks every Claude settings example (cooperative,
  confined, exclusive, with and without prompt-root protection).
- Generation, `--update` and `--check` print a HIGH warning when the live `settings.json` uses the
  key, and `--check-wiring` fails. Only the number of entries is printed, never their values.
- The self-updating-agents design dropped its `excludedCommands` wrapper. Propagating learned agent
  notes now runs outside every agent session, through `--sync-agent-docs` from an operator-installed
  systemd user unit (`scripts/install-agent-doc-sync.sh`).

**Action:** remove `sandbox.excludedCommands` from `.claude/settings.json` and
`.claude/settings.local.json`. Run anything that must run unsandboxed from outside the agent
session.

**Upstream:** the report has been drafted for the operator to send to Anthropic. It has not been
sent yet.

### 2026-09-30 — Brief write roots could widen the next sandbox or run shell in the operator's shell

**Affected:** `1.0.0-rc.7` and earlier; fixed in `1.0.0-rc.8`. This covers every confined or exclusive team. **Severity:**
high.

The brief's `workspace_write_roots` and `coordination_write_roots` went straight into the
sandbox's writable set. Brief files (`.agentteams/brief.json`, `_build-description.json`, or any
`--description` path) are agent-writable. A sandboxed agent could therefore add `~` or `/` to them,
and the operator's next `--update` would emit that root silently. `--check-wiring` then told the
operator to "re-merge".

The goose Linux runner (`.goose/confined-run.example.sh`) was worse: it pasted each root raw into
an operator-run bash script. A root such as `x$(cmd)` ran in the operator's **unsandboxed** shell.

**Fix:**
- **One pinned chokepoint** (`frameworks/_write_roots.py`) refuses these roots in every emitter:
  - `/`, `~`, `$HOME` and their ancestors;
  - the signing-key directory, and anything at, inside or above it;
  - home credential and persistence paths;
  - `~user`;
  - the project's control plane (`.git`, `.claude`, `.goose`, `.codex`, `.github/agents`,
    `.github/hooks`, `sandbox`, `.agentteams`) and any ancestor of the project;
  - shell, SBPL and glob metacharacters.
- **The goose runner** validates every root and also shell-quotes it.
- **Generation names each added root and its source.** A new root outside the project needs the
  operator's `--accept-write-root PATH` (argv only). The accepted baseline is the live
  `.claude/settings.json` for Claude; no other framework has one.
- **The launcher** ro-binds the whole `.claude` directory and protects the goose runner.
- **`permissions.deny`** covers `.claude/settings.hooks.example.json` and the goose runner.
  Status: product-unverified.

**Action:**
1. Run `agentteams --update` outside any sandbox for each confined team.
2. If generation now refuses a root, name a narrower directory. For an external root you intend,
   pass `--accept-write-root`.
3. Check every `SANDBOX WIDENING` line before re-merging.

**Residuals:**
- **The brief files are not write-denied.** Team Builder and agent-updater edit them by design.
  Instead, the widening notice and acceptance are the control.
- **A planted flag.** An agent can plant `--accept-write-root` in a script the operator later
  runs. The notice still names the root and its source.
  Goose and `--sync` have no accepted baseline, so they ask for the flag on every run, and that
  trains operators to bake it into a script. Prefer Claude's live-settings baseline, or a signed grant.
- **The home denylist is not exhaustive.** Acceptance of each external root is the control, and
  any hidden home dir is flagged in the notice.
- **Lexical checks only, with a best-effort `realpath`.** A symlink created after generation is
  resolved by the OS at session time.
- **A sibling-repo coordination root exposes that repo's control plane.**
- **Projects with no `.claude` dir.** There, a confined agent can create `.claude/settings.json`.
- **Launcher side effects.** All of `.claude` is read-only under `confine-run.sh`. A Claude Code
  session run inside the launcher cannot save `.claude/settings.local.json`, and a symlinked `.claude`
  makes the launcher refuse to start (fail-closed). Confined goose and codex agents can no longer
  write `.claude/agents`; run `--sync` into a claude team outside the launcher.
- **A symlink swapped in after generation.** Any in-project root other than `.` can later be
  replaced by a symlink, which bypasses acceptance, because the OS resolves it at session time.
  Tracked as a follow-up.
- **`.git/hooks` stays inside the default root `.`.** Hooks run unsandboxed. Tracked as a
  MEDIUM follow-up.

### 2026-09-30 — Claude sandbox block failed open when the sandbox could not start

**Affected:** `1.0.0-rc.7` and `main` before this fix: every release that emitted the Claude
`sandbox` settings block (`claude:sandbox`, or `privilege_profile: confined`/`exclusive`; `confined`
has been the default since 2026-W39). **Severity:** critical for operators who relied on the block
for confinement.

The emitted block set `enabled: true` and `allowUnsandboxedCommands: false` but not
`failIfUnavailable`. When Claude Code cannot start its sandbox, that combination runs every command
**unsandboxed**, after a single warning. Measured on Linux (Claude Code 2.1.251) with `socat`
missing: "Sandbox disabled … Commands will run WITHOUT sandboxing", and a write to `$HOME`
succeeded.

**Fix:** the block now sets `failIfUnavailable: true` on macOS and Linux targets, so Claude Code
refuses to start instead. It is omitted on native Windows, where the block is advisory.
**Action:** re-run `agentteams --update` and re-merge the `sandbox` block into
`.claude/settings.json`, or add `"failIfUnavailable": true` to your merged `sandbox` object;
`--update` prints a notice while it is missing. On Linux, install `bubblewrap` and `socat`
(`scripts/install-sandbox-deps.sh`). **Update (2026-09-30):** Claude Code's native sandbox is now
**verified end-to-end on Linux** for one host configuration — Ubuntu with
`kernel.apparmor_restrict_unprivileged_userns=1`, bubblewrap 0.11.1, socat 1.8.1.1, Claude Code
2.1.251 — covering write confinement, the switch `denyWrite`, `denyRead` (including `~/`), the
signing-key directory, the `.claude` read-only bind and rename refusal, and the fail-closed
refusal. **Precondition:** on such a host it works only after installing Claude Code's documented
unconfined `/etc/apparmor.d/bwrap` profile (`scripts/test-sandbox-apparmor-userns.sh --persist
documented`), which disables Ubuntu's `bwrap-userns-restrict` host-wide; without it sandboxed
commands fail closed. Other distros and Claude Code versions, macOS, and `bypassPermissions` remain
unverified; see [`docs_src/api-reference/workspace-privilege-scoping.md`](docs_src/api-reference/workspace-privilege-scoping.md).

### 2026-09-30 — An HMAC-signed capability grant could widen the sandbox from inside it

**Affected:** `main` before this fix: every release with cross-workspace capability grants whose
holder team emits the Claude `sandbox` block. **Severity:** high.

Grants were HMAC-signed with the shared `AGENTTEAMS_GRANT_SIGNING_KEY`, and their approver roster
was read from the project root. A Claude-sandboxed agent inherits that key from the shell that
launched it and can create `references/security-approvers.txt` at the project root, so it could
mint a `write` grant that the operator's next `--update` merged into the emitted `allowWrite`.

**Fix:** a grant that widens `allowWrite` must be Ed25519-signed by the operator
(`agentteams --sign-grant`) and verify against the holder team dir's
`references/authorized-verify-keys/<key_id>.pub.pem`, with its approver on the team dir's roster.
HMAC `write` grants are refused (no transitional flag), and a project-root roster is ignored with
a warning. **Action:** run `agentteams --verify-grants` to list refused grants, then follow the
migration steps in the `CHANGELOG.md` entry "only an operator Ed25519 grant widens allowWrite".
Never export `AGENTTEAMS_GRANT_SIGNING_KEY` into the shell that launches `claude`.

**Known limit.** The verify-key store is write-denied only at the framework's default agents dir
(`.claude/agents/references/authorized-verify-keys/`). A team generated with a non-default
`--output` keeps its store elsewhere, so a sandboxed agent could plant its own public key there and
then sign a grant that verifies. Use the default agents dir, or add the store to `denyWrite` and
`permissions.deny` by hand. `--update` warns when `--output` is not the default.

### 2026-09-30 — The copilot and codex team dirs' trust roots were writable from every sandbox

**Affected:** `1.0.0-rc.7` and earlier; fixed in `1.0.0-rc.8`. This covers every project holding a copilot
(`.github/agents`) or codex (`.codex/agents`) agentteams team next to a sandboxed or launcher-confined
agent. **Severity:** medium. The operator's C-5 gate for that team could be forged.

Those team dirs hold the same framework-neutral trust roots as `.claude/agents`: the
`enforce_decision_signing` switch, the verify-key store, the rosters and the team marker. No arm
protected them. A confined agent could, for example, switch signing off and append an unsigned PASS
row, which the operator's next `agentteams --update --output .github/agents --overwrite` would then
honour.

**Fix:**
- The Claude block write-denies each present sibling team dir (`.codex`, `.github/agents`,
  `.goose`). Status: product-verified, Claude Code 2.1.251, Linux, including a `.github` rename
  refusal with a writable `.github/workflows`.
- `permissions.deny` covers their trust-root files. Status: product-unverified.
- The launcher requires and ro-binds them. Status: mechanism-verified.
- The goose Seatbelt profile denies them. Status: mechanism-verified on GitHub `macos-latest` (Darwin 25.6,
  arm64, unnested `sandbox-exec`) for the switch, verify-key store, `.codex/config.toml` and ancestor renames
  (`tests/test_goose_seatbelt_mechanism.py`, #87).
- Copilot and codex teams get the roster stubs and store sentinel whenever the switch is emitted.

**Action:**
1. Run `agentteams --update` for each copilot and codex team, outside any sandbox. Until you do,
   `sandbox/confine-run.sh` refuses the project on Linux.
2. Re-merge the claude team's `sandbox` block.
3. Remove a sibling's `denyWrite` entry BEFORE deleting that team.

**Residuals:**
- **Fake marker.** A planted `build-log.json` in an empty `.github/agents` or `.codex/agents`
  makes the launcher refuse to start. This is denial of service only: nothing is granted. Since #11 the launcher
  and generation name the marker as possibly planted and say how to remove it.
- **Copilot is not fully covered by default.** Its prompt-level roots (`.github/agents/*.agent.md`,
  `.github/copilot-instructions.md`) stay writable through the Edit tool, and the gate hook is
  the only check on them. **Opt-in** `protect_prompt_roots: true` (follow-up #8 phase 2) changes that.
  It adds `Edit(...)` rules for every prompt root, present-only `denyWrite` entries, and
  `confine-run.sh --protect-prompt-roots`. With it on, sandboxed agents cannot author Copilot, Codex or
  goose agents and instructions, and neither can an in-sandbox `--update`. Three limits remain:
  - `.github/workflows` is never covered.
  - The goose macOS Seatbelt profile is deferred.
  - A root created after generation is not in `denyWrite` until the next generation.

  Turning the flag off is detected, not prevented: generation prints `SANDBOX RELAXATION`.
- **Bash in `.github/agents`.** Denying `.github/agents` blocks routine in-sandbox Bash cleanup
  and `sed` there, including of `_build-description.json`, which is tracked separately.
- **`.codex` whole-deny.** It is untested against Codex CLI's own in-project writes.
- **`.codex/config.toml`.** It is protect-if-present, so a launcher-confined process can create
  it while it is absent. Since #11 the launcher and generation warn on its security-relevant keys on every
  run (key-based detection).
- **Teams added later.** A team created after the block was merged stays Bash-writable until you
  re-merge. `--update` and `--check` name it.
- **Profile mismatch.** A pre-existing cooperative claude or goose team still lacks its store and
  rosters, so a launcher emitted by another confined team refuses the project. This predates the
  fix.
- **Out of scope.** `.agents` (agents-md) and Codex's own sandbox are not covered.
