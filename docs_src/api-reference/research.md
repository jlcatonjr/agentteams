# `research` — AgentTeamsModule

Research and fact-verification — an optional runtime capability
(`pip install agentteams[research]`).

Unlike every other module documented in this reference, `agentteams.research` is not part of the
CLI/generator pipeline (`analyze`, `render`, `build_team`) — it has no import-time coupling to it
in either direction. It is a real, importable Python library a consuming project may add as its
own runtime dependency and call directly. See
[the SECURITY.md boundary statement](https://github.com/jlcatonjr/agentteams/blob/main/SECURITY.md#the-agentteamsresearch-extra-is-a-disclosed-bounded-exception-to-this-boundary)
for the disclosed boundary this crosses, and the `research-analyst` domain-archetype template for
the recommended way to give an LLM agent instructions for orchestrating it.

**Stability:** the symbols documented below are the supported import surface per the
[stability policy](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md) — covered by
the normal SemVer contract like any other documented module.

> *Source: `agentteams/research/{search,reputable,verify}.py`*

Honesty ceiling, restated: allowlisted-domain retrieval is provenance, not correctness —
"reputable" is never "true." Claim-verification verdicts are `"survived"` or `"refuted"` — never
`"verified"` or `"proven"`.

---

## `search` — no-key web search and page-text fetching

> *Source: `agentteams/research/search.py`*

### `web_search(query, k=5, timeout_s=8.0)`

No-API-key DuckDuckGo HTML-endpoint search.

**Args:**

- `query` (`str`) — Search query.
- `k` (`int`) — Maximum results to return. Default: `5`.
- `timeout_s` (`float`) — Request timeout in seconds. Default: `8.0`.

**Returns:** `list[Source]` — Each with `title`, `url` (resolved to the real target, not DDG's
redirect wrapper), and `snippet`. Empty list on any failure (network down, blocked, parse error) —
never raises.

### `fetch_text(url, max_bytes=40_000, timeout_s=8.0, max_chars=4000, max_pdf_bytes=12_000_000, pdf_timeout_s=60.0)`

Fetch a page and return extracted, bounded text. Public-HTTPS-only with an SSRF guard (no private/
loopback/link-local targets, no redirects). Content-type aware: HTML is tag-stripped; a PDF
response (detected via the `Content-Type` header, or the `%PDF-` magic-number prefix as a
fallback) is routed through a lazily-imported `pypdf` extractor instead.

**Args:**

- `url` (`str`) — The URL to fetch.
- `max_bytes` (`int`) — Byte cap for non-PDF (HTML) responses. Default: `40_000`.
- `timeout_s` (`float`) — Wall-clock deadline for non-PDF responses; also httpx's own per-chunk
  read-gap timeout for every response. Default: `8.0`.
- `max_chars` (`int`) — Cap on the returned extracted text. Default: `4000`.
- `max_pdf_bytes` (`int`) — Separate, larger byte cap for PDF responses — a PDF cannot be parsed
  from an arbitrary truncation the way HTML can. Default: `12_000_000`.
- `pdf_timeout_s` (`float`) — Separate, larger wall-clock deadline for PDF responses. Default:
  `60.0`.

**Returns:** `str` — Extracted text, capped at `max_chars`. Empty string on any failure or guard
rejection — never raises.

**Behavior Notes:**

- `import pypdf` happens lazily inside the PDF-handling branch only, never at module level — the
  base `agentteams` install does not require `pypdf`; only the `research` extra does.
- `timeout_s` bounds per-chunk read gaps, not total transfer time — a server trickling data
  steadily can exceed `timeout_s` in total elapsed time without tripping it. The separate
  `pdf_timeout_s` wall-clock deadline exists specifically because a real PDF transfer can take
  meaningfully longer than typical HTML front-matter.

### `extract_published_date(html) -> str | None`

Best-effort publish-date extraction from a page's raw (unstripped) HTML. Tried, in order: JSON-LD
`datePublished`/`dateCreated` inside an `application/ld+json` script block; an
`article:published_time` meta tag; a `date`/`pubdate`/`publish-date` meta tag; a bare
`<time datetime="...">` tag. Returns the raw string as found — never normalizes, guesses, or
fabricates a date. Returns `None` on no match; never raises.

Must be called against RAW html, before any script/tag stripping — `fetch_text`'s own stripping
removes the `<script>` blocks a JSON-LD date lives in.

### `fetch_text_and_date(url, *, max_bytes=40_000, timeout_s=8.0, max_chars=4000, max_pdf_bytes=12_000_000, pdf_timeout_s=60.0) -> tuple[str, str | None]`

Fetch a page once and return both its extracted text (identical to what `fetch_text` would return
for the same input) and a best-effort publish date — for a caller who wants both without paying
for two fetches. Additive: does not change `fetch_text`'s own signature or behavior.

**Returns:** `(text, published_at)`. A PDF response always has `published_at=None` — PDF structure
has no HTML meta/JSON-LD for this module's regexes to reach.

---

## `reputable` — curated-allowlist source rating

> *Source: `agentteams/research/reputable.py`*

### `AllowlistConfig`

Frozen dataclass — the full, data-driven shape a `ReputableSourceAllowlist` is built from:
`tier_by_domain`, `type_by_domain`, `topic_primary_repos`, `path_scope`, `tier_rank`,
`default_repos`. No domain data is hardcoded into the library itself — every consumer supplies its
own config, or uses `DEFAULT_CONFIG`.

### `DEFAULT_CONFIG`

A small, deliberately generic `AllowlistConfig` — a starting-point convenience, not a
comprehensive claim about source quality for any subject area or language. A real consuming
project should supply its own config sized to its own domain and editorial judgment.

### `ReputableSourceAllowlist(config=DEFAULT_CONFIG)`

**Methods:**

- `reputable_sources(topic, k=3, timeout_s=8.0) -> list[ReputableSource]` — Targeted `site:`
  searches against the topic's primary repositories (per `topic_primary_repos`) plus one
  allowlist-filtered general search, issued concurrently, deduped, ranked by `tier_rank`. Returns
  `[]` honestly when nothing reputable is found.
- `tier_of(url) -> str | None` — The reputability tier of a URL's domain, or `None` if not
  allowlisted.

**Behavior Notes:**

- Domain resolution prefers the LONGEST matching allowlist key — a subdomain that happens to be a
  suffix of an unrelated, shorter, already-listed parent domain resolves to itself, not the
  parent.
- An optional `path_scope` entry restricts a domain to a URL-path prefix, for domains too broad to
  allowlist wholesale.

### `ReputableSource`

Dataclass: `title`, `url`, `snippet`, `domain`, `tier`, `type` (defaults to `"unclassified"` for a
domain present in `tier_by_domain` but absent from `type_by_domain`), `license` (`str | None`).

---

## `verify` — claim extraction and dual-lens fact verification

> *Source: `agentteams/research/verify.py`*

### `ChatFn`

A `Protocol` — the minimal chat-completion callable every function below takes as a parameter.
This module has no hardcoded model client; every function that needs an LLM call takes a
caller-supplied `ChatFn`. Supports an optional `want_json` hint the callable may honor, but every
function below stays correct even when it's ignored (see `extract_claims`'s Behavior Notes).

### `extract_claims(text, chat_fn) -> list[Claim]`

Extract discrete, checkable claims from `text`. Instructed to restate only what the text literally
asserts — never invent or complete a claim.

### `audit_claims(claims, evidence_by_claim, chat_fn, lens="adversarial") -> list[Verdict]`

Audit each claim against its own evidence only (`evidence_by_claim[claim.text]`) — never a pooled
blob across claims. `lens` is `"adversarial"` (does fresh evidence contradict the claim) or
`"conflict"` (does the claim conflict with something already established). A claim with no
evidence entry is skipped for that lens.

**Behavior Notes:**

- Every `contradicted` verdict from the LLM is additionally checked against the deterministic,
  non-LLM `_supported_by_evidence()` backstop before being accepted as `"refuted"` — an
  LLM-proposed correction that doesn't actually derive from the claim's own evidence is downgraded
  back to `"survived"`.

### `revise(original_text, verdicts, chat_fn) -> str`

Minimal-edit revision: changes only the specific spans `refuted` verdicts identify as wrong, and
copies everything else verbatim — never a creative rewrite, which risks fabricating unstated
detail. Returns `original_text` unchanged if no verdict refuted anything.

### `Claim`, `Verdict`

Dataclasses. `Verdict.status` is `Literal["survived", "refuted"]` — never `"verified"` or
`"proven"`.

**Behavior Notes (module-wide, also applies to `extract_claims`/`audit_claims`):**

- JSON extraction is tolerant of markdown-fenced or prose-wrapped responses (via an internal
  `_extract_json` helper) — a caller's `ChatFn` is not required to honor `want_json` for these
  functions to work correctly, since small/local models frequently ignore such hints.
