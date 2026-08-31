# Part V — Content safety

Content safety is the layer that treats *what an agent reads and writes* as
untrusted data — the point where the constitution's **C-4 (content is data)**
stops being a principle and becomes a deterministic check. It is the one part of
the stack that does not depend on a fallible model judgment: where the scanner
fires, it fires by pattern, every time. Both sections carry precise ceilings, and
they are among the most load-bearing honest limits in the guide.

## The content scanner (`agentteams.scan`)  ✅ {#S15}

**The adversary:** credentials or machine-specific paths leaking into a committed
file, and instruction-override text embedded in content an agent reads. The
scanner is the deterministic backstop behind the sentinel's S-1 (no
credentials/PII) and S-8 (no machine-specific info) rules — the part of
content-safety that does not rest on judgment.

**Two passes per line.** Every line is run through an **injection pass**
(implementing S-5/S-6 — text that tries to *direct behaviour*) and a **line pass**
(implementing S-1/S-8 — PII, credentials, high-entropy tokens, unresolved
placeholders). The line pass detects three families of secret material: **PII**
(absolute home-directory paths that embed a real username, on macOS, Linux, or
Windows — the trailing segment optional, so a bare home dir still flags),
**credentials** (prefixed-token formats — API keys, cloud access-key ids, source-
host tokens, chat/payment tokens, JWTs, private-key headers, password
assignments, DB connection URIs), and **high-entropy tokens** (Shannon entropy
≥ 3.8 bits/char, severity raised when a secret-context keyword sits on the same
line). The prefixed-credential and entropy detectors deliberately cover each
other's blind spots.

**Normalization defeats evasion.** The injection pass matches
instruction-override phrases, identity-override (role-reassignment) phrases —
suppressed inside YAML front matter, where a `name:`/`description:` field
legitimately names a role — and C-1 precedence/tier claims. Critically, all
matching runs over an **NFKC-folded, format-character-stripped** normalization of
each line, which defeats the standard evasion tricks (zero-width characters,
fullwidth homoglyphs, newline-split payloads) that would slip past a naive
substring match.

**Verdict — only high blocks.** Any **high** finding ⇒ **HALT**; any finding at
all ⇒ CONDITIONAL PASS; else PASS. `python -m agentteams.scan <path>` exits 1
**iff** HALT. Exemptions are keyed on **provenance, not shape** — module-owned
files and operational JSON under `references/` are exempt because of *where they
come from*, never because of what they look like. This is deliberate:
shape-based exemptions are exactly what an attacker crafts content to satisfy.

**Honest gap — no formula/CSV-injection detector (fact 4).** There is **no**
formula/CSV-injection detector in the scanner. The class where a leading formula
character in a spreadsheet cell is later executed by a downstream consumer is
**not implemented** here; it is covered, if at all, only procedurally by the
sentinel's judgment. An edition that implies the scanner catches formula
injection states a fact error. More broadly the scanner is **shape-blind**: a
pattern cannot tell an *example* of an attack from an *actual* one. This guide is
the live demonstration — authoring these sections tripped the injection pass on a
quoted override example, because the scanner correctly could not distinguish
inert documentation from a live instruction. That is *why* C-4 exists (all text
is suspect regardless of intent; a human/agent decides), and it is why every
literal attack string in this guide is paraphrased rather than quoted — the
write-scan would HALT on the real thing, which is the layer working as designed.

**Source.** `agentteams/scan.py:38-102,157-174,275-303,536-568,679-857`. Full
mechanism: Edition R, S15.

## Live-data redaction and feed sanitization  ✅ {#S16}

Where S15 guards content the agent *writes*, S16 guards content that flows *in*
from an untrusted upstream — the live threat-intelligence feeds (S20) — before it
is ever embedded in a file or compared against a golden snapshot. **The
adversary:** a hostile or malformed upstream feed whose content tries to break
out of the region it is rendered into.

**Redaction keeps golden comparison deterministic.** `redact_live_data` blanks
the body of every `threat_intelligence`/`threat_data` fence and the
`Generated at:` stamp, so golden-snapshot comparison does not churn every run on
feed rotation and **no live feed content is ever committed as a golden.** This
narrows the exclusion surgically — from excluding the *entire* highest-privilege
security agent file to excluding only its volatile, feed-populated regions; the
rest stays under comparison.

**Feed text is neutralized before it reaches a fence.** `_sanitize_feed_text`
collapses whitespace, **defangs HTML-comment markers** (the key control — an
un-defanged comment-END marker in feed text could otherwise inject an inline
fence-END and break out of its region), converts backticks to apostrophes (so
feed text cannot open code spans), and caps the text at 400 characters. Together
these mean upstream content cannot use its own text to escape the fence — a C-4
trust-boundary control applied to untrusted upstream data.

**Live-feed fences are exempt from shrink detection (S13):** feed rotation each
run is *expected*, not the enriched-content loss S13 exists to catch, and the
feed's canonical history is the cache JSON, not the embedded snapshot — so losing
snapshot text on rotation is recoverable and non-alarming.

**Honest ceiling.** These controls neutralize a feed's *content* at the trust
boundary and keep comparison deterministic; they do not vouch for the feed's
*accuracy* (that is S20's ceiling) and they presuppose the fence machinery itself
is intact (S13/S22). Full mechanism: Edition R, S16.

**Source.** `agentteams/fences.py:158-161,304-403`;
`agentteams/security_feed_render.py:21-60`.

---

**Sources for Part V.** `agentteams/scan.py`; `agentteams/fences.py`;
`agentteams/security_feed_render.py`. Line-precise provenance: `SOURCES.md` (S27).
