# Report: MCP Server Auto-Detection for agentteams

**Status:** Revised after adversarial + conflict audit (audit trail in §9)
**Date:** 2026-06-01
**Scope:** Whether and how agentteams should automatically decide when a project warrants a Model Context Protocol (MCP) server vs. standard agent-overseen API calls, and the standardized form such a server should take.

---

## 1. Current state in agentteams (corrected)

MCP is a **reserved, inert feature token with no consuming code** — not a partially-built scaffold. Verified facts:

- `agentteams/host_features.py:30` registers `mcp` for the `claude` namespace; `:34` registers it for `bridge:copilot-vscode-to-claude`. `bridge:copilot-cli-to-claude` (`:37`) does **not** include `mcp`.
- The module docstring (`host_features.py:10`) names "MCP config" as a planned gated artifact.
- **No emitter reads the token.** `grep` for `is_enabled` and `mcp` across the emit pipeline returns zero hits. The only feature tokens actually consumed are `hooks` and `schedule`, and they are consumed in `bridge.py:307` / `:331` via literal membership tests (`"bridge:copilot-vscode-to-claude:hooks" in features`), **not** via `host_features.is_enabled(...)`.
- Config-file emission today follows a **dedicated-module** pattern: `hooks_emit.py` and `schedule_emit.py` emit their JSON artifacts; the framework adapters (`frameworks/base.py`, `claude.py`) do **not** emit config — they only post-process agent Markdown.
- Tool detection exists: `ingest.py:_detect_tools()` (`:377`) and `_parse_tools()` (`:211`), reading `package.json`, `pyproject.toml`, etc. `ingest.py` is deliberately stdlib-only and conservative.
- `model-routing.schema.json` is the closest precedent for an opt-in, generator-owned JSON artifact (gated, `additionalProperties: false`, `artifact_type` discriminator, versioned).

**Implication:** introducing MCP requires building a new emitter path (`mcp_emit.py`) from scratch, plus a schema and detection logic. It is not "fill in two gaps in an existing scaffold." The current premise — agentic hosts overseeing direct API calls suffice for most work — is **correct and remains the default**. MCP must be *justified*, never defaulted to.

---

## 2. When MCP genuinely helps (the real use cases)

MCP is a *standardization and governance layer* between an agent host and an external system, not "a better API." It earns its cost in five situations:

1. **Cross-host reuse across MCP-supporting hosts.** A capability needed by both a Claude team and a Copilot team is defined once instead of twice as host-specific wrappers. This is the best fit for agentteams' cross-framework mission — *with the caveat in §6.3 that this portability holds only for hosts that actually support MCP.*
2. **Stateful external systems** needing connection pooling, session/auth-token lifecycle, and result caching (databases, ERP/HRIS/CRM, long-lived sessions).
3. **Large or dynamic operation sets** *only when paired with progressive disclosure* (see §4.1) — otherwise this is an efficiency anti-pattern, not a benefit.
4. **Governed credential boundary** — one audited choke point instead of secrets scattered across ad-hoc calls (§4.2).
5. **Non-tool primitives** — exposing *resources* (readable data) or *prompts* (server-supplied templates), not just callable tools.

## 3. When direct API calls are preferred (the default)

- Single-use, single-project, single-framework integration.
- Deterministic, fixed workflow where the AI need not choose actions.
- Few, stable operations.
- Latency-sensitive trivial calls (MCP adds an abstraction hop).
- Already covered by host-native tools (Bash + an SDK).
- **Untrusted third-party data sources** — these do *not* trigger auto-build; they trigger security review (§4.2).

---

## 4. The two prioritization axes — both double-edged

### 4.1 Efficiency

"Many tools → use MCP" is **wrong**. MCP tool *definitions* consume context-window tokens on every request; agents wired to thousands of tools can burn hundreds of thousands of tokens before reading the request. The large savings reported for "code execution with MCP" (an illustrative ~150k→~2k token case, ≈98.7%, from Anthropic's *Code execution with MCP* engineering post [1]) come from **progressive disclosure and in-environment result filtering — not from MCP itself**. A large, always-loaded tool list *hurts* efficiency.

**Consequence for the protocol:** efficiency justifies MCP only when it (a) consolidates repeated boilerplate/auth/context across *many* agents or sessions and (b) commits to progressive disclosure + server-side filtering. The §5 rubric therefore scores *reuse and statefulness*, not raw operation count, and a large-operation-set signal counts **only if** the candidate commits to `progressive_disclosure: lazy` in its §6 definition.

### 4.2 Security

MCP centralizes credentials into one auditable boundary (gain) but makes that server a high-value target and adds attack surface:

- **Tool poisoning** — malicious instructions hidden in tool *metadata* (descriptions, error messages); the most prevalent client-side MCP vulnerability (OWASP, *MCP Tool Poisoning* [2]).
- **Indirect prompt injection** — malicious instructions in returned data.
- **Supply-chain risk** — third-party servers store credentials and run code you did not write.

**What this protocol actually enforces vs. surfaces.** The protocol *enforces* only two things mechanically: least-privilege `scope` binding (an agent sees only servers in its scope) and credential-by-reference (never inline secrets). Everything else — trust-tier accuracy, poisoning review — is **operator-surfaced advisory metadata**, because `trust_tier` is a self-declared label the generator cannot verify, and tool-poisoning detection is out of scope for a generator. The protocol does not claim to *detect* poisoning. Building a *first-party* server for governed internal access is a security gain; adopting a *third-party* server is a risk that is never auto-built (§5).

---

## 5. Protocol 1 — Detecting *when* to develop an MCP server

Run during analyze (not ingest — see §6.2). Produces a per-candidate recommendation. **It recommends; it does not auto-provision a credentialed server** (the deliberate scope decision is named explicitly in §5.4).

### 5.1 Signals

The decision is **gated on two necessary conditions**, not a flat signal count (the flat count was rejected in audit — see §9, H1/A1):

> **Necessary for `BUILD_MCP`:** cross-host reuse **AND** statefulness.
> If either is absent, the default is `USE_DIRECT_API`.

Necessary conditions:
- **Cross-host reuse** — the integration is needed by >1 target host/framework, OR by ≥2 components that would otherwise duplicate the wrapper. (Counted once — "≥2 components" and ">1 host" are the same underlying property and must not double-count.)
- **Statefulness** — auth/session lifecycle, connection pooling, or caching materially benefits the integration.

Tiebreaker signal (applies only when both necessary conditions hold):
- Large/dynamic operation set **and** `progressive_disclosure: lazy` committed → strengthens `BUILD_MCP`. Without the disclosure commitment this signal is *negative* (efficiency anti-pattern, §4.1).

Hard gate → `DEFER_TO_SECURITY_REVIEW` (overrides everything):
- Third-party / unvetted server, OR untrusted data source.
- Any **destructive** (delete/irreversible) operation. Plain `write` is *not* an automatic gate (that would make the gate noise reviewers rubber-stamp); `write` is a caution flag surfaced to the operator.

### 5.2 Decision rule

```
if hard_gate_signal:                          DEFER_TO_SECURITY_REVIEW
elif cross_host_reuse and statefulness:        BUILD_MCP   (+ note disclosure requirement)
else:                                          USE_DIRECT_API
```

Output: advisory entries in the **team-manifest** (`mcp_candidates[]`), surfaced to the operator. Nothing is emitted unless the user opts in via the host-feature token(s) in §6, and any `DEFER_TO_SECURITY_REVIEW` candidate is surfaced for explicit operator authorization (§5.3).

### 5.3 Governance flow (corrected)

The agentteams safety model is **operator-authorization**, not code-enforced agent gating:
- `@security` is a review *sentinel* (`Invokable: no` per `agent-inventory.md:37`); per `bridge-refresh-safety.md:124` it *flags* destructive risk "requiring explicit user authorization." It cannot programmatically "sign off." `DEFER_TO_SECURITY_REVIEW` therefore means: surface to the operator with `@security`'s flag, and proceed only on recorded operator authorization — mirroring the bridge-refresh Pre-Flight pattern.
- Intake is owned by `@team-builder`. `@adversarial` / `@conflict-auditor` run in the **work-summary / closeout** audit lifecycle (`@work-summarizer`), not "intake." MCP-candidate output is naturally audited there.

### 5.4 What was asked vs. what is proposed (named gap)

The request was "automatic detection" **and** "automated development." This protocol delivers **automatic detection and automatic emission of the inert *definition artifact*** (the JSON in §6, which contains no secrets and provisions nothing), but **defers credentialed activation** (writing a live `.mcp.json` that wires real credentials to a network boundary) to explicit operator opt-in. This is a deliberate narrowing of "automated development," justified on MCP-specific grounds: activation provisions a credentialed boundary and is closer to a destructive/outward-facing act than to inert file generation. A future phase could automate activation for `trust_tier: first-party`, `side_effects: read` servers once an enforcement mechanism (§6.4) exists.

---

## 6. Protocol 2 — The *form* of an MCP server definition

New artifact `schemas/mcp-server.schema.json`, modeled on `model-routing.schema.json` (the closest opt-in/generator-owned precedent): `$schema` + `$id`, `additionalProperties: false`, an `artifact_type` const discriminator, and `mcp_server_schema_version`. One file per server per domain. Secrets are **referenced**, never inlined.

```jsonc
{
  "artifact_type": "mcp-server",
  "mcp_server_schema_version": "1.0",
  "server_id": "vk-postgres",
  "domain": "data-access",
  "description": "Read-only query access to the analytics warehouse.",
  "trust_tier": "first-party",        // first-party | third-party-vetted | third-party-untrusted (self-declared)
  "transport": "stdio",                // stdio | http
  "auth": { "mechanism": "env", "credential_ref": "VK_PG_DSN" },  // name only — never the secret
  "tools": [
    { "name": "run_query",
      "description": "Run a parameterized read-only SQL query.",
      "input_schema": { "type": "object", "properties": { "sql": {"type": "string"} } },
      "side_effects": "read" }        // read | write | destructive
  ],
  "resources": [],
  "prompts": [],
  "scope": ["data-analyst-expert"],    // least-privilege: which agents may bind
  "progressive_disclosure": "lazy",    // lazy | eager — controls context cost (§4.1)
  "security_review": { "required": true, "authorized_by": null, "authorized_at": null }
}
```

### 6.1 Emission (corrected layer)

A **dedicated `mcp_emit.py` module**, following the `hooks_emit.py` / `schedule_emit.py` precedent — **not** the framework adapters (which have no config-emission responsibility). It:
- Renders Claude Code's native `.mcp.json` when MCP is enabled for a Claude target.
- For a host without MCP support, emits a documented **stub + falls back to direct-API guidance** (see §6.3) — it does not silently claim the capability transferred.
- Records which agents bind which servers per `scope` (least privilege).

### 6.2 Gating (corrected)

Gate on **both** namespaces, matching the live `bridge.py` membership-test style — because the canonical path per CLAUDE.md is the copilot-vscode→claude **bridge**, where a `claude:mcp`-only gate would never fire:

```python
mcp_on = ("claude:mcp" in features) or ("bridge:copilot-vscode-to-claude:mcp" in features)
```

### 6.3 Non-MCP host fallback (the portability caveat made concrete)

MCP is host-agnostic **only across MCP-supporting hosts**. When a target host lacks MCP, `mcp_emit.py` emits: (a) the inert definition artifact (for documentation/portability), and (b) a generated agent instruction to use the equivalent direct API call. The capability does not silently vanish, but neither is it transparently portable — the report does not overclaim universality.

### 6.4 Enforcement honesty

`security_review.required: true` is **operator-surfaced advisory**, not a code block — there is no enforcement mechanism in the pipeline today, and a field inside the emitted file is self-attesting. A real block would require an external source of truth (e.g., a recorded authorization in the run, CI gate) that does not yet exist. Until built, `security_review` surfaces to the operator; it does not "enforce."

### 6.5 Pipeline integration points

| Phase | Change | As-built status |
|---|---|---|
| `analyze.py` | run §5 gating → `team-manifest.schema.json` `mcp_candidates[]` | **Wired.** Calls `agentteams.mcp_detect.detect_mcp_candidates`; populates `mcp_candidates` only when the description declares `mcp_hints` (manifests without MCP integrations are unchanged). |
| `agentteams/mcp_detect.py` (new) | the §5 rubric, extracted as a pure stdlib module (sibling to `host_features.py`) | **Built + tested.** Fail-closed on missing/unknown `trust_tier`/`max_side_effect`; strict boolean coercion; slug dedup; exposes a structured `efficiency_risk` signal. |
| `schemas/` | add `mcp-server.schema.json` (modeled on `model-routing.schema.json`); add `mcp_candidates`/`mcp_servers` to **team-manifest**; add optional `mcp_hints` to **project-description** (`additionalProperties: false`, so this is an explicit reviewed property addition) | **Built.** `mcp-server.schema.json` carries `allOf` `if/then` clauses giving the hard gate real schema backing: a destructive tool or third-party `trust_tier` forces `security_review.required = true`, and `transport: http` requires `auth`. `credential_ref` is pattern-constrained so an inline connection string cannot validate. |
| `agentteams/mcp_emit.py` (new) | gate per §6.2; emit the inert artifact; surface `security_review` | **Built + tested, intentionally NOT wired into the build pipeline.** Wiring the emitter into `bridge.py`/`build_team.py` *is* the deferred credentialed-activation step (§5.4); it is therefore a standalone module a future opt-in phase invokes. Self-enforces inertness (rejects inline-secret-shaped `credential_ref` and schema-invalid servers), fails closed on `activation_status`, and defaults `overwrite=False` so operator authorization records are never clobbered. `mcp_servers[]` is not yet populated by any pipeline stage. |
| governance | operator authorization per §5.3 (not code-enforced) | Unchanged: advisory; the schema `allOf` is the only mechanical backing. |

---

## 7. Recommendation

1. Keep direct, host-overseen API calls as the **default**.
2. Implement the §5 detection rubric as **gated on cross-host-reuse AND statefulness** (not a flat signal count), with destructive-only hard gating.
3. Auto-emit the **inert definition artifact**; defer **credentialed activation** to operator opt-in (§5.4).
4. Build emission as a dedicated `mcp_emit.py`, gated on both `claude:mcp` and `bridge:copilot-vscode-to-claude:mcp`.
5. Be honest about limits: `trust_tier` is self-declared, `security_review` is advisory until an external enforcement gate exists, and portability holds only across MCP-supporting hosts.

---

## 8. Limitations / not yet addressed

- **Versioning & drift:** generated servers need version pinning analogous to the bridge `v=N` fences; not yet designed.
- **Lifecycle/teardown:** decommissioning, credential rotation, and `@cleanup`'s handling of a generated `.mcp.json` are out of scope here (`bridge-refresh-safety.md:§VI` binds `@cleanup` for emitted artifacts — MCP config must be added to that scope).
- **Validation/testing:** how a generated server is validated before binding (cf. `eval-suite.schema.json`) is unspecified.
- **Maintenance cost:** the human cost of owning a generated network service is real and unquantified here.
- **False positives at scale:** the gated rule reduces but does not eliminate them; the necessary-condition gate is the mitigation, not a guarantee.
- **Tool-poisoning detection:** explicitly out of scope; only least-privilege scope + credential-by-reference are enforced.

---

## 9. Audit trail

This report was passed through an adversarial audit and a conflict audit. Both grounded findings in the codebase. Material findings and their resolution:

**Adversarial (logic/soundness):**
- **A1 (HIGH) — flat `score = positives − negatives, ≥2` is naive, gameable, double-counts cross-host reuse, and can suppress the strongest case.** → Replaced with a two-necessary-condition gate (§5.1/§5.2).
- **A2 (HIGH) — §4.1 efficiency caveat contradicted the "large operation set" positive signal.** → Operation-count signal now counts only with a progressive-disclosure commitment, else negative (§5.1).
- **A3 (HIGH) — "advisory-only" silently redefined the user's "automated development" ask.** → Named explicitly in §5.4; middle tier added (auto-emit inert artifact, defer activation).
- **A4 (HIGH) — "security_review enforced, not advisory" was unenforceable.** → Corrected to operator-surfaced advisory; enforcement mechanism named as future work (§6.4).
- **A5 (MED) — trust-tier / poisoning claims non-actionable.** → Scoped down to what is actually enforced (§4.2).
- **A6 (MED) — "host-agnostic artifact the bridge lacks" overclaimed.** → Softened to "across MCP-supporting hosts"; stub fallback made concrete (§6.3).
- **A7 (MED) — hard gate over-triggered on all writes.** → Reserved for *destructive* only (§5.1).
- **A8/A9 (LOW) — missing limitations; uncited stats.** → §8 added; citations [1][2] added.

**Conflict (alignment with existing infra):**
- **C1 (HIGH) — "scaffolding/seams exist" was false; the token is inert with no consumer.** → §1 rewritten.
- **C2 (HIGH) — adapters cannot emit `.mcp.json`; config emission lives in dedicated `*_emit.py` modules.** → §6.1 now specifies a new `mcp_emit.py`.
- **C3 (HIGH) — `is_enabled(features,"claude","mcp")` gate would never fire on the canonical bridge path, which uses literal `in features` checks.** → §6.2 gates on both namespaces in the live style.
- **C4 (MED) — code-enforced `@security` sign-off contradicts its `Invokable: no` review-sentinel role and the operator-authorization model.** → §5.3 corrected.
- **C5 (MED) — "@adversarial/@conflict-auditor in intake" misnamed the lifecycle.** → Corrected to work-summary/closeout; intake owned by `@team-builder` (§5.3).
- **C6/C7 (MED) — schema must follow `model-routing.schema.json` conventions; `additionalProperties:false` makes field additions explicit; input-description vs team-manifest were conflated.** → §6 and §6.5 corrected.
- **C9 (LOW) — scoring placed in stdlib-only `ingest.py` where the needed signals don't exist.** → Moved scoring to `analyze.py`; ingest collects raw signals only (§6.5).
- **C-positive — the advisory/opt-in posture aligns with the `--bridge-merge`-default safety culture.** → Retained.

**Citations**
[1] Anthropic, *Code execution with MCP: building more efficient AI agents* — https://www.anthropic.com/engineering/code-execution-with-mcp
[2] OWASP, *MCP Tool Poisoning* — https://owasp.org/www-community/attacks/MCP_Tool_Poisoning

---

## 10. As-built implementation (2026-W23)

The preparations in §6/§7 were implemented step-by-step, each step passed through `@adversarial` + `@conflict-auditor` and revised before the next (plan: `tmp/by-week/2026-W23/mcp-auto-detection.*`). All deliverables are additive and inert; no existing emitter behavior changed.

**Files:**
- `schemas/mcp-server.schema.json` — server-definition schema with `allOf` hard-gate backing.
- `schemas/team-manifest.schema.json` — added `mcp_candidates[]` (structured `signals`, incl. `efficiency_risk`) and `mcp_servers[]` (`$ref` to the server schema).
- `schemas/project-description.schema.json` — added optional `mcp_hints[]` (`trust_tier` required so absence can't fail open).
- `agentteams/mcp_detect.py` — pure §5 rubric; wired into `agentteams/analyze.py`.
- `agentteams/mcp_emit.py` — gated inert emitter; standalone (activation deferred).
- `tests/test_mcp_detect.py`, `tests/test_mcp_emit.py`, `tests/test_mcp_schema.py` — 38 tests.

**Audit-driven hardening (beyond the original design):** schema `allOf` gates (step-1 audit H2); fail-closed detection on unknown security fields + strict boolean coercion + slug dedup (step-2 audit Q4/Q5/Q6); emitter inertness self-enforcement, sibling `activation_status` instead of a schema-violating injected key, and `overwrite=False` (step-3 audit F1/F2/F4/F5).

**Test status:** 38 new tests pass; full suite 1172 passed, with 2 pre-existing failures in `tests/test_memory_index_relevance.py` (BM25 accuracy thresholds) confirmed unrelated to this work (they fail identically on clean `HEAD`).

**Not done (by design):** `mcp_emit.py` is not wired into `bridge.py`/`build_team.py`, and no stage populates `mcp_servers[]` — these constitute credentialed activation, deferred to explicit operator opt-in per §5.4.
