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
design-time-only and unchanged. The optional `research` extra
(`pip install agentteams[research]`) — and its heavier `browser` sibling
(`pip install agentteams[browser]`, layered on top of `research`, adding real Playwright-driven
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
