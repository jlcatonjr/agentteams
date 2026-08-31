# Part VII — Threat intelligence and red team

## Threat-intelligence watch  ✅ {#S20}

On init and on update, agentteams pulls a set of **live threat feeds** and folds
them into the security agent's watch, alongside a **static** knowledge baseline
that ships in source. The distinction matters: the live feeds change between
runs and carry a freshness contract (below); the static content is versioned in
the module and updated deliberately.

**The feeds and what backs each one.** Fetches are not naive HTTP GETs — each is
constrained by an exact-match HTTPS **host allowlist**, per-host response **size
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

The allowlist is checked against the **effective, post-redirect URL host**, not
the URL originally requested — a control stated in-module as intentionally
exact-match only, "to avoid trusting unrelated or compromised subdomains"
(`agentteams/security_refs.py:42-70`; the render/fetch path at
`agentteams/security_refs.py:710-917`). This is the C-4 trust boundary applied
to upstream infrastructure: the feed data is untrusted input, and the fetch
layer treats it as such.

**Six intel-bearing placeholders.** The watch renders **six** intel-bearing
placeholders into the security agent — current threats, the prevention playbook,
LLM-specific threats, OSV package findings, the full watch JSON, and the source
registry — plus freshness fields and a payload digest
(`agentteams/security_feed_render.py:21-144`;
`agentteams/templates/universal/security-vulnerability-watch.reference.template.md`).
The reference is explicit about what these represent: a snapshot "valid as of
`Generated at`… not a static authoritative baseline." The watch is a
point-in-time view of a moving target, not a settled fact.

**Freshness TTL = 24 hours.** The snapshot has a hard **24-hour TTL**. Any of
the following sets the snapshot status to `stale`: a stale cache; a fetch
failure with no cache; an offline run with no cache; an age greater than 24h; or
an unparseable timestamp. When stale, the render prepends a stale-data banner
and the status feeds the S12 intelligence-freshness gate (which blocks the whole
generation run — see S12). The in-module banner names it plainly as a warning
that the content "may not reflect current threat status."

**The digest bind — why a relabelled timestamp cannot fake freshness.** The
payload digest is computed **last**, over the six intel-bearing placeholders. It
is bound into the S12 gate (`SECURITY_DATA_PAYLOAD_DIGEST` must equal the
SHA-256 of those six placeholders). The consequence is the load-bearing one:
relabelling a stale snapshot's timestamp to "now" **does not** buy a passing
freshness check, because the gate compares the digest, and reproducing the
digest requires regenerating the placeholders — i.e. actually fetching fresh
data. Freshness is tied to the *content*, not to a self-asserted timestamp.

**Honest ceiling.** The watch reports what the upstream feeds said at fetch time
and stamps it as provisional-by-time; it is not an authoritative vulnerability
baseline, and a run older than its TTL is treated as stale rather than trusted.
The allowlist and size bounds harden the fetch against a redirecting or
oversized upstream; they do not vouch for the *correctness* of what a legitimate
feed returns.

**Source.** `agentteams/security_refs.py:42-70,710-917`;
`agentteams/security_feed_render.py:21-144`;
`agentteams/templates/universal/security-vulnerability-watch.reference.template.md`.

## Red-team methodology  ✅ {#S21}

The red-team infrastructure is a **cycle**, not a test run — its distinguishing
feature is that it audits its own audit. It measures the *actual* controls in
this repository against explicitly stated attacker tiers and records the
outcome per probe.

**The seven-phase cycle.** A full cycle runs:

1. **Attack** — run probes against an isolated copy of the tree.
2. **Review** — assess what the probes found.
3. **Plan** — decide remediation.
4. **Audit-plan** — check the plan before implementing.
5. **Implement** — apply fixes.
6. **Evaluate the red team** — audit the audit (the phase-6 self-audit, below).
7. **Remediate** — close findings.

The cycle re-enters phase 1 and repeats **until two consecutive rounds find
nothing new**. A **standing/scheduled** run does phases **1, 2, and 6 only** — it
measures and reports, and never remediates on its own. **Phase 6 — auditing the
audit — is what distinguishes this from a test suite**
(`agentteams/redteam/cycle.py:1-215`).

**Attacker tiers.** A control is meaningful only against a stated tier; a claim
with no tier is not a measurement.

| Tier | The attacker |
|---|---|
| **T0** | supplies content the agent *reads* (injected text) |
| **T1** | an in-repo agent with **write + execute** |
| **T2** | the operator's own shell |

**Five outcome classes.** Each probe resolves to exactly one of: **DEFENDED**,
**PARTIAL**, **EXPLOITED**, **DOCUMENTED-LIMIT**, or **OUT-OF-TIER**. An unknown
outcome **raises** — a typo in an outcome label is treated as a defect in the
instrument, not as a measurement
(`agentteams/redteam/registry.py:36-55`; `agentteams/redteam/runner.py:39-79`).

**Probes attack a copy, then assert byte-identity.** Probes run against an
**isolated copy** of the real tree and then assert the live tree is
**byte-identical** afterward. The merge is used as a *measurement*
(RESTORED / PRESERVED / REFUSED), not as a safety net — the point is to observe
what the control did, not to rely on the copy to protect the real files.

**Counts refuse to exist without a denominator.** A count with no denominator
and no canonical population source is rejected at the type level. This is the
type-level fix for the defect that once **hid 719 exposed agents**: a coverage
number is only meaningful against the population it was computed over, so the
machinery refuses to emit a bare count.

**Phase-6 self-audit — the six ways a red team fools itself (F-1..F-6).** The
self-audit checks six failure modes of the auditing machinery itself
(`agentteams/redteam/selfaudit.py:33-101`):

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
`--accept-probe-baseline` records a new probe baseline and is **refused under
`--dry-run`** (accepting a baseline is a write, and a dry run must not
half-perform it). The exit codes are ordered so a **broken instrument outranks a
finding**: **exit 2 (the instrument itself is broken) outranks exit 1 (a finding
exists)**. A red team that cannot trust its own tooling must report *that* first.

**Honest ceiling.** The cycle measures the controls that exist against the tiers
it states; it cannot measure an attack no probe expresses, or a tier no probe
targets. Phase 6 exists precisely because an audit can fool itself — F-1..F-6
are named failure modes, not a proof of their own absence — and the byte-identity
assertion tells you what happened to the copy, not that every future attack
leaves the tree untouched.

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
