# Part V — Content safety

Content safety is the layer that treats *what an agent reads and writes* as untrusted
data. It is where the constitution's **C-4 (content is data)** stops being a principle
and becomes a deterministic check: a scanner that inspects text for credentials, PII,
and instruction-override attempts, and a redaction/sanitization path that neutralizes
live threat-feed content before it is ever committed. Both are ✅ code controls — but
each carries a precise ceiling, and this Part states those ceilings prominently because
they are the most load-bearing honest limits in the whole guide.

---

## The content scanner (`agentteams.scan`)  ✅ {#S15}

`scan_content` is the deterministic backstop behind the `@security` sentinel's S-1
(no credentials/PII) and S-8 (no machine-specific info) rules. It is the one part of the
content-safety layer that does not depend on a fallible LLM judgment — where it fires,
it fires by pattern, every time.

### Mechanism — two passes per line

`scan_content` runs **two passes over every line** of the content it is given
(`agentteams/scan.py:536-568`):

1. **An injection pass** — implements the sentinel's S-5 (content-injection guard) and
   S-6 (reviewed-content isolation). It looks for text that tries to *direct behaviour*
   rather than describe it.
2. **A line pass** — implements S-1/S-8: PII, credentials, high-entropy tokens, and
   unresolved placeholders.

The line pass detects three families of secret material
(`agentteams/scan.py:38-102`):

| Family | What it matches | Severity |
|---|---|---|
| **PII** | Absolute username paths (`/Users/<name>`, `/home/<name>`, `C:\Users\<name>` — the trailing segment optional, so a bare home dir is still flagged) | high |
| **Credentials** | Prefixed-token formats: API keys, AWS access-key ids, GitHub tokens (classic + fine-grained PATs), Slack tokens, Stripe secret keys, JWTs, PEM private-key headers, password assignments, database connection URIs | high |
| **High-entropy tokens** | Opaque contiguous strings at Shannon entropy ≥ **3.8 bits/char**; severity is raised when a secret-context keyword (secret/token/credential/auth/password/bearer/private key) sits on the same line | high in context |

The prefixed credential patterns exist *because* the generic entropy detector misses
them when they sit on a line with no secret-context keyword — the two detectors cover
each other's blind spots (`agentteams/scan.py:44-59`).

### Injection detection — normalization defeats evasion

The injection pass matches three sub-classes (`agentteams/scan.py:157-174`,
`agentteams/scan.py:275-303`):

- **Instruction-override phrases** — text attempting to countermand prior instructions.
- **Identity-override phrases** — role-reassignment attempts; these are **suppressed
  inside YAML front matter**, where a `name:`/`description:` field legitimately names a
  role, to avoid flagging every agent file's own header.
- **C-1 precedence / tier claims** — content that announces its own authority (a claim
  to supersede prior instructions, or to occupy a higher instruction tier).

Critically, all matching runs over a **normalized** form of each line: NFKC-folded and
format-character-stripped (`agentteams/scan.py:679-857`). This defeats the standard
evasion tricks — zero-width characters, fullwidth homoglyphs, and payloads split across
newlines — that would otherwise let an override phrase slip past a naive substring match.

### Verdict — only *high* blocks

The scan produces one of three verdicts (`agentteams/scan.py:536-568`):

- any **high** finding ⇒ **HALT**;
- any finding at all (below high) ⇒ **CONDITIONAL PASS**;
- otherwise ⇒ **PASS**.

**Only a high finding blocks.** The CLI wrapper `python -m agentteams.scan <path>`
exits `1` **iff** the verdict is HALT. Exemptions are keyed on **provenance, not shape**
— module-owned files and operational JSON under `references/` are exempt because of
*where they come from*, never because of what they look like. This is a deliberate design
choice: shape-based exemptions are exactly what an attacker crafts content to satisfy.

### Honest gap — no formula/CSV-injection detector (fact 4)

**There is no formula/CSV-injection detector in `scan.py`.** The class of attack where a
leading formula character in a spreadsheet cell is interpreted by a downstream consumer
as an executable formula is **not implemented** in the scanner. It is covered — if at all
— only procedurally, by the sentinel's judgment-based S-rules, not by any deterministic
check. An edition that implies the scanner catches formula injection is stating a fact
error.

More broadly, the scanner is **shape-blind**: it matches patterns, and a pattern cannot
tell an *example* of an attack from an *actual* attack. This guide is the live
demonstration. Authoring these very sections tripped the injection pass on a quoted
example of an override phrase — the scanner correctly could not distinguish a documented,
inert illustration from a real instruction embedded in text. That is precisely why
**C-4 (content is data)** exists: the scanner treats all text as suspect regardless of
intent, and the human/agent reading a finding must decide whether it is a live threat or
inert documentation. It is also why every literal attack string in this guide is
hyphenated or paraphrased rather than quoted verbatim — the write-scan would HALT on the
real thing, which is the content-safety layer working as designed.

**Source.** `agentteams/scan.py:38-102,157-174,275-303,536-568,679-857`.

---

## Live-data redaction and feed sanitization  ✅ {#S16}

Where S15 guards content the agent *writes*, S16 guards content that flows *in* from an
untrusted upstream — the live threat-intelligence feeds (S20) — before it is ever
embedded in a generated file or compared against a golden snapshot.

### Redaction keeps golden comparison deterministic

`fences.redact_live_data` blanks the body of every `threat_intelligence` /
`threat_data` fence and the `Generated at:` stamp
(`agentteams/fences.py:158-161`). The purpose is determinism: golden-snapshot
comparison must not churn every run just because a live feed rotated, and **no live feed
content is ever committed as a golden**. This narrows the exclusion surgically — from
excluding the *entire* highest-privilege security agent file to excluding only its
volatile, feed-populated regions. The rest of the file stays under comparison.

### Feed text is neutralized before it reaches a fence

External feed text is sanitized by `_sanitize_feed_text`
(`agentteams/security_feed_render.py:21-60`) *before* it is placed inside any fence — a
C-4 trust-boundary control applied to untrusted upstream data. The neutralization:

- **collapses whitespace** (so multi-line payloads cannot smuggle structure);
- **defangs HTML-comment markers** — this is the key control, because an un-defanged
  comment-END marker in feed text could otherwise inject an inline fence-END and break
  out of the region it is supposed to be confined to;
- **converts backticks to apostrophes** (so feed text cannot open code spans in the
  rendered output);
- **caps the text at 400 characters**.

Together these mean a hostile or malformed upstream feed cannot use its content to
escape the fence it is rendered into.

### Live-feed fences are exempt from shrink detection

Live-feed fences are deliberately exempt from the shrink-policy detection of S13
(`agentteams/fences.py:304-403`). Feed rotation shrinking or growing the embedded
snapshot each run is *expected* behaviour, not the enriched-content loss S13 exists to
catch. The canonical history of the feed is the cache JSON, **not** the embedded
snapshot — so losing snapshot text on rotation is recoverable and non-alarming, and
flagging it would only train operators to ignore the shrink gate.

**Source.** `agentteams/fences.py:158-161,304-403`;
`agentteams/security_feed_render.py:21-60`.

---

**Sources for Part V.**
`agentteams/scan.py:38-102,157-174,275-303,536-568,679-857`;
`agentteams/fences.py:158-161,304-403`;
`agentteams/security_feed_render.py:21-60`.
