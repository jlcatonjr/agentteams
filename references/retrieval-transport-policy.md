# Retrieval Transport Policy — external retrieval is CLI-mediated

**Status:** Standing operator decision. Binding until an operator says otherwise.
**Recorded:** 2026-07-30
**Binds:** `@orchestrator`, `@security`, `@retrieval-integrator`, `@research-analyst`,
`@framework-adapters-expert`, `@tool-doc-researcher`, `@reference-manager`
**Enforced by:** `tests/test_retrieval_no_mcp.py`

---

## The decision

External retrieval in this project — web search, page fetching, browser rendering, and
scholarly lookup — is performed **through this package's own CLI**:

```
python -m agentteams.research search  "<query>"
python -m agentteams.research fetch   "<url>"
python -m agentteams.research browser "<url>" [--headed] [--screenshot PATH]
python -m agentteams.research scholar "<query>" [--citations]
```

Two things are **deliberately not used** as the retrieval transport:

1. **MCP servers.** No MCP server is to be added to provide search, fetch, or any other
   retrieval capability.
2. **Host-native web tools** (`WebSearch` / `WebFetch` in Claude Code, and equivalents in
   other hosts). No vocabulary token maps to them.

## Why this is written down

The 2026-07-30 retrieval review
(`references/plans/external-retrieval-expansion-2026-07-30.report.md`) identified the absence
of a retrieval transport as its largest finding, and recommended two expansions that this
policy declines: adding a `web` token bound to host-native `WebSearch`/`WebFetch` (report
§5 Tier 1.1) and treating MCP as a pluggable retrieval transport (report §5 Tier 4).

Both were declined by operator directive. Recording the refusal matters because **a gap and a
decision look identical from the outside.** Without this file, the next capable agent reading
that report will read "no retrieval transport wired up" as an oversight and helpfully add one.
That is the specific failure this document exists to prevent.

## What the decision buys

- **One auditable egress path.** Every outbound request this framework's teams make goes
  through `agentteams/research/`, where the SSRF guard (`search.is_public_https`), the host
  allowlists (`scholarly._ALLOWED_HOSTS`, `security_refs._ALLOWED_RESPONSE_HOSTS`), the size
  caps, and the degrade-don't-raise contract all live. A host-native web tool bypasses all of
  them; an MCP server replaces them with someone else's.
- **No third-party code in the retrieval path.** The MCP report's own §4.2 names tool
  poisoning (malicious instructions in tool *metadata*) as the most prevalent client-side MCP
  vulnerability, alongside indirect prompt injection and supply-chain risk. A third-party
  search server is exactly that risk profile. This policy avoids it by construction rather
  than by review.
- **No always-loaded tool definitions.** Per the MCP report's §4.1, MCP tool definitions
  consume context on every request. A CLI invocation costs tokens only when used.
- **Portability.** The same command works from Claude Code, Copilot CLI, Goose, a shell
  script, or CI. Host-native tools work in exactly one host.

## What it costs — stated honestly

- **No host-managed rate limiting or result curation.** Claude Code's `WebSearch` is a
  managed service; this package's chain is two free DuckDuckGo endpoints plus whatever the
  operator configures. That is why `agentteams/research/backends.py` exists and why
  `agentteams/research/cache.py` exists — they are compensating controls for a limitation this
  policy accepts.
- **Retrieval requires a Bash-family grant.** An agent cannot search without permission to run
  a command. This is mitigated, not eliminated, by the scoped grant below.

## The `retrieval` tool token

`agentteams/frameworks/claude.py` maps the vocabulary token `retrieval` to:

```
Bash(python -m agentteams.research:*)
```

— a scoped Bash permission granting this CLI and nothing else. The point is that an agent
needing to look something up does **not** thereby gain arbitrary shell execution.

**Known, unresolved uncertainty.** Whether a given Claude Code version honours the
parenthesised scope inside *sub-agent* front matter (as opposed to slash-command front matter,
where it is long-established) is not verifiable from inside this repository. If a host ignored
the scope, it would read the entry as plain `Bash` — granting **more** than intended. Two
consequences follow, and both are load-bearing:

1. `retrieval` is granted only to agents whose charter is external verification and which a
   reviewer would accept holding `execute` anyway. Today: `tool-doc-researcher` and
   `reference-manager`.
2. It is **never** granted to a read-only auditor (`tools: ['read', 'search']`). Their
   read-only invariant is a constitutional property, and network egress is a side effect.

`agentteams/framework_research.py` already exists to track this class of upstream drift; when
it can confirm the behaviour either way, this section should be updated rather than deleted.

## Revisiting this

This is a decision, not a law of nature. It should be revisited if:

- an operator explicitly asks for host-native web tools or an MCP retrieval server; **or**
- the CLI chain's coverage proves inadequate in a way `backends.py` cannot fix by adding
  another key-free backend.

Note that the existing MCP suitability rubric would **not** approve a search server on its own
terms: `agentteams/mcp_detect.py` gates `BUILD_MCP` on `cross_host_reuse AND statefulness`, and
a search server is stateless — it classifies as `USE_DIRECT_API`. Enabling MCP-based retrieval
would therefore require amending that rubric, not merely adding configuration. Anyone
proposing the change should say so explicitly rather than routing around it.

## Related

- `references/plans/external-retrieval-expansion-2026-07-30.report.md` — the review this
  responds to.
- `references/mcp-auto-detection-report.md` — the MCP suitability rubric and its security
  analysis (§4.1 efficiency, §4.2 security).
- `agentteams/templates/universal/external-retrieval-quality-gate.reference.template.md` —
  the mandatory audit loop for any claim resting on retrieved information.
