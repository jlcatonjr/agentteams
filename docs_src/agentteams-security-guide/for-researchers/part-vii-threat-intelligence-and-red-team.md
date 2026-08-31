# Part VII — Threat intelligence and red team

This Part is where a security researcher meets the two controls most directly in
their own idiom: a threat-intelligence watch, and a red-team methodology that
audits its own audit. Both are rendered in full, because the *methodology* — the
tiers a control is measured against, the outcome classes, and the six ways an
audit fools itself — is the most transferable content in the guide.

## Threat-intelligence watch  ✅ {#S20}

**What it is for.** On init and on update, agentteams pulls live threat feeds and
folds them into the security agent's watch, alongside a **static** knowledge
baseline versioned in source. The distinction matters to a reviewer: live feeds
change between runs and carry a freshness contract; static content is updated
deliberately.

**The feeds, and the trust boundary on the fetch.** The upstream feed is
*untrusted input* (C-4 applied to infrastructure), so fetches are not naive HTTP
GETs — each is constrained by an exact-match HTTPS **host allowlist** checked
against the **effective, post-redirect URL host**, per-host response **size
bounds**, and IDNA normalization, so a redirect or a compromised look-alike
subdomain cannot slip content past the boundary.

| Source | Kind | Notes |
|---|---|---|
| **CISA KEV** (Known Exploited Vulnerabilities) | live | `www.cisa.gov` |
| **FIRST EPSS** (exploit-prediction scores) | live | `api.first.org` |
| **MITRE CVE** | live | `cveawg.mitre.org` |
| **NVD CVSS** | live, **optional, rate-limited** | `services.nvd.nist.gov` |
| **OSV.dev** | live, **package-level** — only when `tools` are given | `api.osv.dev` |
| **OWASP LLM Top 10 (2025)** | **static** (source-versioned) | + a static source registry |

**Six intel-bearing placeholders.** The watch renders **six** intel-bearing
placeholders into the security agent — current threats, the prevention playbook,
LLM-specific threats, OSV package findings, the full watch JSON, and the source
registry — plus freshness fields and a payload digest. The reference is explicit
about their status: a snapshot "valid as of `Generated at`… not a static
authoritative baseline." The watch is a point-in-time view of a moving target.

**Freshness TTL = 24 hours.** Any of a stale cache, a fetch failure with no
cache, an offline run with no cache, an age > 24h, or an unparseable timestamp
sets the status to `stale`; the render prepends a stale-data banner and the status
feeds the S12 gate (which blocks the whole generation run).

**The digest bind — why a relabelled timestamp cannot fake freshness.** The
payload digest is computed **last**, over the six placeholders, and bound into the
S12 gate. The consequence is load-bearing: relabelling a stale snapshot's
timestamp to "now" **does not** buy a passing freshness check, because the gate
compares the digest, and reproducing the digest requires regenerating the
placeholders — i.e. actually fetching fresh data. Freshness is tied to *content*,
not to a self-asserted timestamp.

**Honest ceiling.** The watch reports what the upstream feeds said at fetch time
and stamps it provisional-by-time; it is **not** an authoritative vulnerability
baseline, and a run older than its TTL is treated as stale rather than trusted.
The allowlist and size bounds harden the fetch against a redirecting or oversized
upstream; they do **not** vouch for the *correctness* of what a legitimate feed
returns.

**Source.** `agentteams/security_refs.py:42-70,710-917`;
`agentteams/security_feed_render.py:21-144`;
`agentteams/templates/universal/security-vulnerability-watch.reference.template.md`.

## Red-team methodology  ✅ {#S21}

The red-team infrastructure is a **cycle, not a test run** — and its
distinguishing feature is that it **audits its own audit.** For a researcher, this
is the section worth reading closely: it measures the *actual* controls in this
repository against explicitly stated attacker tiers, and it treats a broken
instrument as more serious than a finding.

**The seven-phase cycle.** Attack → Review → Plan → Audit-plan → Implement →
**Evaluate the red team** → Remediate, re-entering phase 1 until two consecutive
rounds find nothing new. A standing/scheduled run does phases **1, 2, and 6
only** — it measures and reports, never remediating on its own. **Phase 6 —
auditing the audit — is what distinguishes this from a test suite.**

**Attacker tiers — a control is meaningful only against a stated tier.** A claim
with no tier is not a measurement:

| Tier | The attacker |
|---|---|
| **T0** | supplies content the agent *reads* (injected text) |
| **T1** | an in-repo agent with **write + execute** |
| **T2** | the operator's own shell |

This is the same discipline a threat modeler applies by hand: name the adversary's
capability before claiming a control defeats them. The in-scope adversary of S1
maps to T0/T1.

**Five outcome classes.** Each probe resolves to exactly one of **DEFENDED**,
**PARTIAL**, **EXPLOITED**, **DOCUMENTED-LIMIT**, or **OUT-OF-TIER**. An unknown
outcome **raises** — a typo in an outcome label is a defect in the *instrument*,
not a measurement. Note `DOCUMENTED-LIMIT`: an honest-ceiling result is a
first-class outcome, not a failure to hide.

**Probes attack a copy, then assert byte-identity.** Probes run against an
**isolated copy** of the real tree and then assert the live tree is
**byte-identical** afterward. The merge is used as a *measurement*
(RESTORED / PRESERVED / REFUSED), not as a safety net — the point is to observe
what the control did, not to lean on the copy to protect the real files.

**Counts refuse to exist without a denominator.** A count with no denominator and
no canonical population source is rejected at the type level — the type-level fix
for the defect that once **hid 719 exposed agents.** A coverage number is only
meaningful against the population it was computed over.

**Phase-6 self-audit — the six ways a red team fools itself (F-1..F-6).** These
are the transferable failure modes of any audit machinery, and a researcher can
apply them to their own tooling directly:

| ID | Failure mode it catches |
|---|---|
| **F-1** | a verifier that always passes — every verifier needs a sensitivity test and a negative control |
| **F-2** | a fix wired to one of two call sites — every fix must reach every call path |
| **F-3** | hand-rolling what the tool provides — no hand-rolled target, descriptor, or VCS resolution |
| **F-4** | a coverage claim with an unexamined denominator — every count carries the population it was computed over |
| **F-5** | a probe that got blinder, not better — every probe whose behaviour changed is re-validated for intent |
| **F-6** | accepting a weakness with no ledger diff — every accepted weakness has a named reason |

**CLI and the exit-code hierarchy.** `--redteam` runs the audit;
`--redteam-freshness-check` checks the standing run's currency;
`--accept-probe-baseline` records a new baseline and is **refused under
`--dry-run`.** The exit codes encode the methodology's core value: **exit 2 (the
instrument itself is broken) outranks exit 1 (a finding exists).** A red team that
cannot trust its own tooling must report *that* first.

**Honest ceiling.** The cycle measures the controls that exist against the tiers
it states; it **cannot** measure an attack no probe expresses, or a tier no probe
targets. Phase 6 exists precisely because an audit can fool itself — F-1..F-6 are
named failure modes, not a proof of their own absence — and the byte-identity
assertion tells you what happened to the copy, not that every future attack leaves
the tree untouched.

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
`agentteams/redteam/selfaudit.py`; `agentteams/redteam/cycle.py`. Line-precise
provenance: `SOURCES.md` (S27).
