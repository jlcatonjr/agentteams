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

> *Source: `agentteams/research/{search,reputable,verify,browser}.py`*

`browser` (below) is a further, heavier exception within this already-exceptional subpackage: it
is gated behind its own separate `agentteams[browser]` install (not folded into `agentteams[research]`)
and is deliberately **not** re-exported from `agentteams.research`'s package-level `__all__` the
way `search`/`reputable`/`verify` are — reach it via `from agentteams.research.browser import
browser_fetch`, or `python -m agentteams.research browser <url>`, so that a plain `import
agentteams.research` never risks pulling in Playwright.

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

### `is_public_https(url) -> bool`

The SSRF guard `fetch_text`/`fetch_text_and_date` apply before every request: `https` scheme only,
and the hostname must not resolve to a private, loopback, link-local, or reserved IP address.
Public (not module-private) specifically so `agentteams.research.browser` can reuse this exact
check as its own pre-navigation gate — the one deliberate cross-submodule import of a
security-relevant helper in this package. A browser context needs a **second**, per-request
version of this same check in addition (see `browser` below) — this function alone is necessary
but not sufficient there, since it only ever runs once, before the first request.

---

## `browser` — real-browser rendering for JavaScript-heavy pages

> *Source: `agentteams/research/browser.py`* — requires the separate `agentteams[browser]` extra
> (`pip install agentteams[browser]`) **and** a one-time `playwright install chromium` (the extra
> installs the `playwright` Python package only; browser binaries are a required second step it
> cannot perform). Not imported by `agentteams.research`'s own `__init__.py` — see the note at the
> top of this page.

Use this only once `fetch_text` has been tried and found insufficient — i.e. the page needs
JavaScript to populate its real content (a client-rendered app, a "loading..." skeleton, content a
framework injects after the initial HTML). Slower and heavier than a plain fetch by design; it is
the escalation tier, not the default.

### `browser_fetch(url, *, headed=False, wait_until="networkidle", timeout_s=20.0, max_chars=4000) -> str`

Render `url` in a real Chromium browser and return extracted, bounded text — same tag-strip/
unescape/whitespace-collapse text shape as `fetch_text`, so the two are consistent regardless of
which one a caller used.

**Args:**

- `headed` (`bool`) — Show the browser window. Default `False` (headless): this is normally a
  one-shot call from an agent's shell tool on a server/container/CI runner with no display
  attached, where a headed launch would simply fail. `headed=True` is for a human operator
  co-located with a real display who wants to watch (debugging, demos, a manual login/2FA step) —
  it changes nothing about the function's return value; the calling agent has no way to perceive a
  rendered window either way.
- `wait_until` (`str`) — One of Playwright's navigation wait conditions: `"load"`,
  `"domcontentloaded"`, `"networkidle"`. Default `"networkidle"` — a better fit than `"load"` for
  the JS-hydration-heavy pages this function exists for, which often fire `load` before
  client-side rendering has populated real content. Known tradeoff: `"networkidle"` never fires on
  a page with continuous background activity (long-polling, websockets); pass `"load"` or
  `"domcontentloaded"` for those.
- `timeout_s` (`float`) — Navigation timeout. Default `20.0`.
- `max_chars` (`int`) — Cap on the returned extracted text. Default `4000`.

**Returns:** `str` — Extracted text. Empty string on any failure: `playwright` not installed,
browser binaries not installed, the initial URL failing the SSRF guard, navigation timeout, or any
other Playwright-raised error. Never raises.

### `browser_screenshot(url, output_path, *, headed=False, wait_until="networkidle", timeout_s=20.0) -> bool`

Render `url` and save a full-page screenshot to `output_path`. Same args and never-raises contract
as `browser_fetch`; returns `True` on success, `False` on any failure.

**Behavior Notes:**

- **SSRF guard, two layers — not one.** The initial URL is checked with `is_public_https` before a
  browser is even launched, **and** every subsequent request the live page attempts (redirects,
  subresources, page-initiated JS `fetch`/`XHR`) is re-checked by the same guard via a Playwright
  `page.route` handler. A single pre-navigation check alone is insufficient for a real browser,
  which follows redirects by default and runs arbitrary page JavaScript capable of issuing its own
  requests to other hosts — a pre-navigation-only check (as a plain HTTP fetch tool correctly
  uses) does not cover either case.
- **Named, undefended residual: DNS rebinding.** This guard's DNS resolution and Chromium's own
  subsequent connection are two separate resolutions, not atomically the same one — stated
  honestly as a known gap, not silently assumed away.
- Content is capped at 2,000,000 characters immediately after `page.content()` retrieval, before
  any text extraction — a cap applied *after* the DOM is materialized, not a true streaming cap
  like `fetch_text`'s (Playwright's `page.content()` is synchronous and always returns the full
  rendered DOM; there is no earlier point to interrupt at).
- Before installing or first using this capability in a project, see
  [`references/skill-generation.reference.md`](https://github.com/jlcatonjr/agentteams/blob/main/agentteams/templates/universal/skill-generation.reference.template.md)'s
  Security Rule S-9 (Pathway Safety Verification) gate — the same review path any other new,
  durable CLI capability in a generated team goes through.

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
