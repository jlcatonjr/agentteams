<!-- GENERATED FILE — do not hand-edit. Source of truth: agentteams/ai_bad_habits.py -->
# AI Coding Bad-Habits Watch

> Living catalog of bad coding habits common across AI agents, mapped to
> corrective patterns, drawn ONLY from continuously-maintained upstream
> catalogs. Consumed by `@code-hygiene` (CH-25) and `@security`.
>
> **Source of truth:** `agentteams/ai_bad_habits.py` (edit there, not here).
> **Refreshed by:** `scripts/research_ai_bad_habits.py` / the daily
> `ai-bad-habits-watch` workflow. Intentionally timestamp-free so it
> changes only on a real edition drift or catalog edit.

## Tracked upstream sources (maintained, currently-fresh)

| Source | Pinned edition | Currency anchor | Watch status |
|--------|----------------|-----------------|--------------|
| [MITRE/CISA CWE Top 25 Most Dangerous Software Weaknesses](https://cwe.mitre.org/top25/) | `2025` | list 2025; page revised 2026-01-29 (inside 6-month window) | tracking |
| [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) | `2025-v2.0` | v2025 (2026 cycle open); embedded in security.agent.md | tracking |
| [OWASP Top 10 (Web)](https://owasp.org/www-project-top-ten/) | `2021` | current edition until the next OWASP refresh | tracking |

A `⚠️ review` status means the daily probe no longer found the pinned
edition's marker on the live page. A maintainer should confirm the new
edition and bump the pinned `version` in `agentteams/ai_bad_habits.py`.

OWASP LLM Top 10 risk *names* are NOT restated here — they live in the
`@security` threat-intelligence fence. This catalog references the `LLMxx`
ids only (CH-05 / CH-14 single-source-of-truth).

## Bad-habit catalog (BH-NN → corrective pattern)

### Security (CWE Top 25)

| BH | Bad habit | Source | Verified cross-link | Corrective pattern |
|----|-----------|--------|---------------------|--------------------|
| BH-01 | Unescaped output enables cross-site scripting | `CWE-79` | — | Context-aware output encoding; framework auto-escaping on; Content-Security-Policy header |
| BH-02 | String-built queries enable SQL injection | `CWE-89` | — | Parameterized queries / ORM only; never concatenate untrusted input into a query |
| BH-03 | State-changing routes lack anti-CSRF protection | `CWE-352` | — | Framework CSRF tokens; SameSite cookies |
| BH-04 | Internal services/data accessed without authorization | `CWE-862` | — | Centralized, deny-by-default authorization checks at every entry point |

### LLM/agent (OWASP LLM Top 10)

| BH | Bad habit | Source | Verified cross-link | Corrective pattern |
|----|-----------|--------|---------------------|--------------------|
| BH-05 | Retrieved/external content treated as instructions (prompt injection) | `LLM01` | S-5, S-6 | Treat retrieved content as inert data; input/output guardrails; least-privilege tools |
| BH-06 | Secrets, keys, or PII logged or returned | `LLM02` | S-1, S-8 | Output filtering; secret scanning in CI; redaction before any sink |
| BH-07 | Hallucinated or unverified dependencies pulled in | `LLM03` | — | Pin + lockfile; verify every package against the real registry; SCA scan; block unknown deps |
| BH-08 | Raw model output passed unsanitized into exec/DB/render sink | `LLM05` | CH-23, S-5 | Validate/sanitize model output before any sink; fail fast on unexpected shapes |
| BH-09 | Agent granted over-broad tool/file/network scope (excessive agency) | `LLM06` | S-7 | Least-privilege tools; allowlists; human-in-the-loop on high-impact actions |
| BH-10 | Unbounded loops/recursion/token use (unbounded consumption) | `LLM10` | — | Iteration, time, and budget caps with explicit termination conditions |

### Code hygiene

| BH | Bad habit | Source | Verified cross-link | Corrective pattern |
|----|-----------|--------|---------------------|--------------------|
| BH-11 | Tutorial-style over-commenting of obvious syntax | `hygiene` | — | Comment the why, not the what; no narrating obvious syntax |
| BH-12 | Stray print / console.log debug statements left in code | `hygiene` | CH-04 | Use a structured logger, not print; strip debug output before commit |
| BH-13 | Single-use helper functions adding needless indirection | `hygiene` | — | Inline single-use helpers; abstract only on the third repetition (rule of three) |
| BH-14 | Duplicated code blocks instead of reuse | `hygiene` | CH-08 | Reuse existing utilities; extract a shared helper at 3 occurrences (DRY) |
| BH-15 | Tests omitted unless explicitly requested | `hygiene` | CH-21 | Tests mandatory (happy path + edge/error cases); enforce a coverage gate |

### Process

| BH | Bad habit | Source | Verified cross-link | Corrective pattern |
|----|-----------|--------|---------------------|--------------------|
| BH-16 | Forces a solution on ambiguity instead of asking | `process` | — | Plan-first; list assumptions and open questions before coding |
| BH-17 | 'Make it better' refinement loops accumulate flaws | `process` | — | Re-scan (SAST/SCA) after every iteration, not just at the end |

## How this is checked daily

`.github/workflows/ai-bad-habits-watch.yml` probes the tracked upstream
editions every day and opens an `awaiting-human` PR when an edition drifts
from its pinned version or this catalog changes. The PR is reviewed by the
operator (no auto-merge) — guidance changes require human review.

