# Part V — Content safety

Content safety treats *what an agent reads and writes* as untrusted data — where
C-4 (content is data) becomes a deterministic check. Both controls here are ✅
code controls with precise ceilings.

## The content scanner (`agentteams.scan`)  ✅ {#S15}

`scan_content` is the deterministic backstop behind the sentinel's S-1
(credentials/PII) and S-8 (machine-specific info) rules. Where it fires, it fires
by pattern, every time.

**Run it:**

```
python -m agentteams.scan <path>     # exit 1 iff the verdict is HALT
```

**Two passes per line** (`agentteams/scan.py:536-568`): an **injection pass**
(S-5/S-6) and a **line pass** (S-1/S-8 — PII, credentials, high-entropy tokens,
unresolved placeholders).

The line pass detects three families of secret material
(`agentteams/scan.py:38-102`):

| Family | What it matches | Severity |
|---|---|---|
| **PII** | absolute username paths (a `/Users/<name>` shape; trailing segment optional, so a bare home dir is flagged) | high |
| **Credentials** | prefixed-token formats: API keys, AWS access-key ids, GitHub tokens (classic + fine-grained), Slack tokens, Stripe secret keys, JWTs, PEM private-key headers, password assignments, DB connection URIs | high |
| **High-entropy tokens** | opaque strings at Shannon entropy ≥ **3.8 bits/char**; raised when a secret-context keyword sits on the same line | high in context |

The prefixed patterns exist *because* the entropy detector misses them on a line
with no secret keyword — the two detectors cover each other's blind spots
(`agentteams/scan.py:44-59`).

**Injection detection** matches instruction-override phrases, identity-override
phrases (suppressed inside YAML front matter, where `name:`/`description:`
legitimately name a role), and C-1 precedence/tier claims
(`agentteams/scan.py:157-174,275-303`). All matching runs over a **normalized**
form — NFKC-folded, format-character-stripped
(`agentteams/scan.py:679-857`) — which defeats zero-width, fullwidth-homoglyph,
and newline-split evasion.

**Verdict — only *high* blocks** (`agentteams/scan.py:536-568`): any **high** ⇒
**HALT**; any finding at all ⇒ **CONDITIONAL PASS**; else **PASS**. Exemptions are
keyed on **provenance, not shape** — module-owned files and operational JSON under
`references/` are exempt by *where they come from*, never by what they look like
(shape-based exemptions are what an attacker crafts content to satisfy).

**Honest gap — no formula/CSV-injection detector exists.** The class where a
leading formula character in a spreadsheet cell is later interpreted as an
executable formula is **not implemented** in `scan.py`; it is covered, if at all,
only procedurally by the sentinel's S-rules. Claiming the scanner catches formula
injection is a fact error.

More broadly the scanner is **shape-blind** — a pattern cannot tell an *example*
of an attack from a *real* one. This guide is the live demonstration: authoring
these sections tripped the injection pass on a documented, inert illustration of
an override phrase, which the scanner correctly could not distinguish from a real
directive. That is exactly why **C-4** exists and why every attack string in this
guide is hyphenated/paraphrased rather than quoted — the write-scan would HALT on
the real thing.

**Source.** `agentteams/scan.py:38-102,157-174,275-303,536-568,679-857`.

## Live-data redaction and feed sanitization  ✅ {#S16}

Where S15 guards content an agent *writes*, S16 guards content flowing *in* from
untrusted upstream feeds (S20) before it is embedded or compared to a golden.

**Redaction keeps golden comparison deterministic.**
`fences.redact_live_data` blanks the body of every `threat_intelligence` /
`threat_data` fence and the `Generated at:` stamp
(`agentteams/fences.py:158-161`), so golden-snapshot comparison doesn't churn on
feed rotation and **no live feed content is committed as a golden**. This narrows
the exclusion from the whole highest-privilege agent file to only its volatile,
feed-populated regions.

**Feed text is neutralized before it reaches a fence** by `_sanitize_feed_text`
(`agentteams/security_feed_render.py:21-60`) — a C-4 trust-boundary control on
untrusted upstream data:

- **collapses whitespace** (multi-line payloads can't smuggle structure);
- **defangs HTML-comment markers** — the key control: an un-defanged comment-END
  marker could otherwise inject an inline fence-END and break out of its region;
- **converts backticks to apostrophes** (feed text can't open code spans);
- **caps the text at 400 characters**.

**Live-feed fences are exempt from shrink detection (S13)**
(`agentteams/fences.py:304-403`): feed rotation each run is expected, and the
feed's canonical history is the cache JSON, not the embedded snapshot — so
flagging rotation would only train operators to ignore the shrink gate.

**Source.** `agentteams/fences.py:158-161,304-403`;
`agentteams/security_feed_render.py:21-60`.

---

**Sources for Part V.**
`agentteams/scan.py:38-102,157-174,275-303,536-568,679-857`;
`agentteams/fences.py:158-161,304-403`;
`agentteams/security_feed_render.py:21-60`.
