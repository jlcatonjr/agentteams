# Part VII — Threat intelligence and red team

## Threat-intelligence watch  ✅ {#S20}

On init/update, agentteams pulls **live threat feeds** and folds them into the
security agent's watch, alongside a **static** knowledge baseline shipped in
source. Fetches are guarded: an exact-match HTTPS **host allowlist** (checked on
the *effective, post-redirect* URL host), per-host response **size bounds**, and
IDNA normalization (`agentteams/security_refs.py:42-70`; render/fetch at
`agentteams/security_refs.py:710-917`).

| Source | Kind |
|---|---|
| **CISA KEV** | live (`www.cisa.gov`) |
| **FIRST EPSS** | live (`api.first.org`) |
| **MITRE CVE** | live (`cveawg.mitre.org`) |
| **NVD CVSS** | live, **optional, rate-limited** (`services.nvd.nist.gov`) |
| **OSV.dev** | live, **package-level** — only when `tools` are given (`api.osv.dev`) |
| **OWASP LLM Top 10 (2025)** | **static** (+ a static source registry) |

It renders **six intel-bearing placeholders** (current threats, prevention
playbook, LLM threats, OSV packages, the full watch JSON, the source registry)
plus freshness fields and a payload digest
(`agentteams/security_feed_render.py:21-144`). The snapshot is "valid as of
`Generated at`… not a static authoritative baseline."

**Freshness TTL = 24h.** Stale cache / fetch-failed-no-cache / offline-no-cache /
age > 24h / unparseable timestamp sets status `stale`, prepends a stale-data
banner, and feeds the S12 gate. The **payload digest is computed last**, over the
six placeholders, so a relabelled timestamp cannot fake freshness (S12) — the
digest bind ties freshness to *content*.

**Honest ceiling.** The watch reports what upstream feeds said at fetch time,
stamped provisional-by-time; a run older than its TTL is treated as stale, not
trusted. The allowlist and size bounds harden the fetch against a redirecting or
oversized upstream; they do not vouch for the *correctness* of what a legitimate
feed returns.

**Source.** `agentteams/security_refs.py:42-70,710-917`;
`agentteams/security_feed_render.py:21-144`;
`agentteams/templates/universal/security-vulnerability-watch.reference.template.md`.

## Red-team methodology  ✅ {#S21}

The red team is a **cycle** that audits its own audit — it measures the *actual*
controls in this repo against stated attacker tiers and records a per-probe
outcome.

**Run it:**

```
agentteams --redteam                    # exit 2 = broken instrument OUTRANKS exit 1 = finding
agentteams --redteam-freshness-check    # is the standing run current?
agentteams --accept-probe-baseline      # record a new probe baseline (refused under --dry-run)
```

The exit-code ordering is the operator-facing contract: **exit 2 (the instrument
itself is broken) outranks exit 1 (a finding exists)** — a red team that cannot
trust its own tooling must report *that* first. `--accept-probe-baseline` is a
write and is **refused under `--dry-run`** (a dry run must not half-perform it).

**Seven-phase cycle** (`agentteams/redteam/cycle.py:1-215`): Attack → Review →
Plan → Audit-plan → Implement → **Evaluate the red team** → Remediate, re-entering
phase 1 until **two consecutive rounds find nothing new**. A **standing/scheduled**
run does phases **1, 2, 6 only** — measure and report, never remediate. Phase 6 —
auditing the audit — is what distinguishes it from a test suite.

**Attacker tiers** (a control is meaningful only against a stated tier):

| Tier | The attacker |
|---|---|
| **T0** | supplies content the agent *reads* (injected text) |
| **T1** | an in-repo agent with **write + execute** |
| **T2** | the operator's own shell |

**Outcome classes:** DEFENDED / PARTIAL / EXPLOITED / DOCUMENTED-LIMIT /
OUT-OF-TIER; an unknown outcome **raises** (a typo is a defect, not a measurement)
(`agentteams/redteam/registry.py:36-55`; `agentteams/redteam/runner.py:39-79`).

Probes attack an **isolated copy** and assert the live tree is **byte-identical**
after; the merge is a *measurement* (RESTORED/PRESERVED/REFUSED), not a safety
net. **Counts refuse to exist without a denominator** and a canonical population
source — the type-level fix for the defect that once hid 719 exposed agents.

**Phase-6 self-audit — F-1..F-6** (`agentteams/redteam/selfaudit.py:33-101`):

| ID | Failure mode it catches |
|---|---|
| **F-1** | a verifier that always passes |
| **F-2** | a fix wired to one of two call sites |
| **F-3** | hand-rolling what the tool provides |
| **F-4** | a coverage claim with an unexamined denominator |
| **F-5** | a probe that got blinder, not better |
| **F-6** | accepting a weakness with no ledger diff |

**Honest ceiling.** The cycle measures the controls that exist against the tiers
it states; it cannot measure an attack no probe expresses or a tier no probe
targets. F-1..F-6 are named failure modes, not a proof of their own absence, and
the byte-identity assertion tells you what happened to the copy — not that every
future attack leaves the tree untouched.

**Source.**
`agentteams/templates/universal/redteam-methodology.reference.template.md:22-239`;
`agentteams/redteam/registry.py:36-55`; `agentteams/redteam/runner.py:39-79`;
`agentteams/redteam/selfaudit.py:33-101`; `agentteams/redteam/cycle.py:1-215`.

---

**Sources for Part VII.** `agentteams/security_refs.py`;
`agentteams/security_feed_render.py`;
`agentteams/templates/universal/security-vulnerability-watch.reference.template.md`;
`agentteams/templates/universal/redteam-methodology.reference.template.md`;
`agentteams/redteam/registry.py`; `agentteams/redteam/runner.py`;
`agentteams/redteam/selfaudit.py`; `agentteams/redteam/cycle.py`.
