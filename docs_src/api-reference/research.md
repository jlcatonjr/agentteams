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

> *Source: `agentteams/research/{search,backends,cache,scholarly,reputable,news,verify,browser}.py`*

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

When the endpoint *challenges* a request rather than answering it, this retries with
progressively broadened queries, down to a floor, before giving up (see `web_search_verbose` for
why, and for how to tell the two cases apart).

### `web_search_verbose(query, k=5, timeout_s=8.0)`

`web_search` plus an explanation of any fallback or block.

DuckDuckGo answers a challenged request with **HTTP 202** and an interstitial page rather than an
error status, so `raise_for_status()` never fires and the page simply parses to nothing. Plain
`web_search` therefore cannot distinguish *"you were blocked"* from *"nothing matched"* — both are
`[]`. That ambiguity is load-bearing for an agent: read as "no such information exists", it
abandons an answerable question. Measured 2026-07-24: a long specific query was challenged on 4/4
attempts while a shortened form returned 10 results.

**Args:** identical to `web_search`.

**Returns:** `tuple[list[Source], str | None]` — the results, plus `None` for an ordinary search or
a short human-readable note saying the query was broadened, or that the endpoint challenged the
request and the empty list is **not** evidence that nothing matched.

`python -m agentteams.research search` prints this note to **stderr**, keeping the JSON on stdout
parseable.

> **Known gap:** the note is prose, so a caller distinguishing block-from-absence must
> substring-match it — use `web_search_with_provenance` below for a structured form. Also, a
> `429` rate-limit still arrives via `httpx.HTTPError` and remains indistinguishable from
> "nothing matched" — only the `202` case is handled.

### `web_search_with_provenance(query, k=5, timeout_s=8.0)`

`web_search_verbose` plus a structured `SearchProvenance` record — the machine-readable form of
"how was this obtained", which the external-retrieval quality gate requires a summary to be able
to state.

**Returns:** `tuple[list[Source], str | None, SearchProvenance]`, where `SearchProvenance` carries
`backend`, `cached`, `query_used`, `backends_tried`, and `challenged`.

Three of those change what a claim is worth:

- **`cached`** — `true` means up to 6 hours old. Re-run with `AGENTTEAMS_RESEARCH_NO_CACHE=1`
  before presenting a time-sensitive claim as current.
- **`query_used`** — when it differs from the query you issued, the endpoint challenged the
  original and the tool retried with a **broader** one. Broader queries return less precise
  results; treat such a hit as weaker evidence and say so.
- **`backend`** — not all backends index the same corpus.

`python -m agentteams.research search` prints all of this to **stderr** on every query:

```
provenance: backend=duckduckgo cached=false query_used='...' tried=duckduckgo,ddg_lite
```

### `fetch_text(url, max_bytes=400_000, timeout_s=8.0, max_chars=4000, max_pdf_bytes=12_000_000, pdf_timeout_s=60.0)`

Fetch a page and return extracted, bounded text. Public-HTTPS-only with an SSRF guard (no private/
loopback/link-local targets, no redirects). Content-type aware: HTML is tag-stripped; a PDF
response (detected via the `Content-Type` header, or the `%PDF-` magic-number prefix as a
fallback) is routed through a lazily-imported `pypdf` extractor instead.

**Args:**

- `url` (`str`) — The URL to fetch.
- `max_bytes` (`int`) — Byte cap on the DOWNLOAD for non-PDF (HTML) responses. Default: `400_000`. Distinct from `max_chars` (which bounds returned text): set too low, a large page is truncated to its `<head>`/navigation and extraction silently yields chrome with no error — raised from `40_000` on 2026-07-24 after a Wikipedia article extracted to 342 chars of pure navigation.
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
- `python -m agentteams.research fetch <url> [--max-chars N] [--max-bytes N] [--timeout-s S]`
  invokes this function directly and prints `{"url": ..., "text": ...}` as JSON to stdout.

### `extract_published_date(html) -> str | None`

Best-effort publish-date extraction from a page's raw (unstripped) HTML. Tried, in order: JSON-LD
`datePublished`/`dateCreated` inside an `application/ld+json` script block; an
`article:published_time` meta tag; a `date`/`pubdate`/`publish-date` meta tag; a bare
`<time datetime="...">` tag. Returns the raw string as found — never normalizes, guesses, or
fabricates a date. Returns `None` on no match; never raises.

Must be called against RAW html, before any script/tag stripping — `fetch_text`'s own stripping
removes the `<script>` blocks a JSON-LD date lives in.

### `fetch_text_and_date(url, *, max_bytes=400_000, timeout_s=8.0, max_chars=4000, max_pdf_bytes=12_000_000, pdf_timeout_s=60.0) -> tuple[str, str | None]`

Fetch a page once and return both its extracted text (identical to what `fetch_text` would return
for the same input) and a best-effort publish date — for a caller who wants both without paying
for two fetches. Additive: does not change `fetch_text`'s own signature or behavior.

**Returns:** `(text, published_at)`. A PDF response always has `published_at=None` — PDF structure
has no HTML meta/JSON-LD for this module's regexes to reach.

### `strip_html_to_text(html, max_chars) -> str`

Shared HTML→text extraction: drops `script`/`style`/`nav`/`footer`/`header` blocks, strips the
remaining tags, unescapes entities, collapses whitespace, and caps the result at `max_chars`.
Public (not module-private) specifically because `agentteams.research.browser` reuses this exact
extraction — applied there to a browser's rendered `page.content()` instead of a raw HTTP response
body — so `fetch_text` and `browser_fetch` return text in the same shape regardless of which one a
caller used.

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

## `backends` — the search fallback chain

> *Source: `agentteams/research/backends.py`*

`web_search` does not call one endpoint; it walks a chain. The zero-configuration chain is
`duckduckgo` → `ddg_lite`, both key-free — a fallback that requires setup is not a fallback.

| Backend | Enabled by | Notes |
|---|---|---|
| `duckduckgo` | always | `html.duckduckgo.com`; the historical default |
| `ddg_lite` | always | `lite.duckduckgo.com`; a different renderer, challenged independently |
| `searxng` | `AGENTTEAMS_SEARXNG_URL` | Operator-chosen instance; many disable the JSON API, which degrades to a skip |
| `brave` | `AGENTTEAMS_BRAVE_API_KEY` | Key travels as a header, never a query parameter |

Configured backends always rank **after** the free ones, so an operator who supplies a key does
not pay for every query.

### `available_backends()` / `backend_names()`

The backends usable right now, in chain order. Useful for reporting provenance.

**Chain semantics.** Every backend is tried on the *original* query before the query is altered
at all — switching provider loses nothing, whereas broadening discards search terms. Only when
the whole chain has been **challenged** does `web_search` broaden, and then progressively, down
to the floor. An honest zero across all backends does **not** trigger broadening: a query that
genuinely matched nothing will only match less precise nothing when shortened.

---

## `cache` — TTL disk cache for retrieved results

> *Source: `agentteams/research/cache.py`*

Search, fetch, and scholarly results are cached for 6 hours by default under
`references/research-cache/` (gitignored). Disable with `AGENTTEAMS_RESEARCH_NO_CACHE=1`;
relocate with `AGENTTEAMS_RESEARCH_CACHE_DIR`.

This cache persists **untrusted third-party bytes**, so its failure behaviour is part of its
contract: filenames are SHA-256 digests only (no external text reaches a path component), writes
are atomic, and a corrupt, oversized, or expired entry is treated as a **miss** rather than an
error. A broken cache degrades to "no cache", never to a broken call.

### `cache_enabled() -> bool`

Whether caching is active in this process — implements the `AGENTTEAMS_RESEARCH_NO_CACHE` behavior
described above: `False` when that variable is set to anything other than an explicit
`"0"`/`"false"`/empty value, `True` otherwise.

### `cache_dir() -> Path`

The directory cache entries live in — implements the `AGENTTEAMS_RESEARCH_CACHE_DIR` behavior
described above: the path it names when set, else `references/research-cache` under the current
working directory. Not created here; `store()` creates it lazily on first write.

### `make_key(kind, *parts)`

Every part participates in the digest, so changing `k` or the backend set produces a different
key rather than silently reusing a differently-shaped result.

### `load(key, ttl_s=DEFAULT_TTL_S)`

### `store(key, value)`

### `purge_expired(ttl_s=DEFAULT_TTL_S)`

---

## `scholarly` — OpenAlex, Crossref, arXiv

> *Source: `agentteams/research/scholarly.py`*

A general web search returns a *page about* a paper; these APIs return the paper's own record,
with a DOI. All three are key-free — no credential is read, required, or supported.

### `scholarly_search(query, k=5, sources=SOURCES, timeout_s=10.0) -> list[ScholarlyWork]`

Queries the chosen indexes concurrently and deduplicates by DOI, then by normalised title for
records that reach one index without a DOI. One index failing never loses the others' results.

`python -m agentteams.research scholar <query> [-k N] [--sources ...] [--timeout-s S]
[--citations]` invokes this and prints the works as JSON to stdout; `--citations` additionally
attaches a `format_citation()` line to each record.

### `search_openalex(query, k=5, timeout_s=10.0)`

### `search_crossref(query, k=5, timeout_s=10.0)`

### `search_arxiv(query, k=5, timeout_s=10.0)`

Single-source variants. Each returns `[]` on any failure and never raises.

### `ScholarlyWork`

`title`, `authors`, `year`, `doi`, `url`, `abstract`, `venue`, `source`.

### `format_citation(work) -> str`

A compact citation line built only from fields the source actually provided. A missing year
renders `(n.d.)` rather than a guess.

**Honesty ceiling.** A scholarly index hit is **provenance**: the work exists, by these authors,
published there. It is not a claim the work is correct, replicated, relevant, or **un-retracted**
— retraction status is not checked. Nothing is inferred, normalised, or filled in when a source
omits it, which is what makes the output safe to build a bibliography from.

**Polite pool.** OpenAlex and Crossref grant higher rate limits to requests carrying a contact
address. Set `AGENTTEAMS_RESEARCH_CONTACT_EMAIL` to opt in. It is deliberately never derived from
git config or any other ambient source — transmitting an operator's address to a third party is
their decision to make explicitly.

---

## `reputable` — curated-allowlist source rating

> *Source: `agentteams/research/reputable.py`*

### `AllowlistConfig`

Frozen dataclass — the full, data-driven shape a `ReputableSourceAllowlist` is built from:
`tier_by_domain`, `type_by_domain`, `topic_primary_repos`, `path_scope`, `tier_rank`,
`default_repos`. No domain data is hardcoded into the library itself — every consumer supplies its
own config, or uses `DEFAULT_CONFIG`. `type_by_domain` values are drawn from the module's
`VALID_TYPES` frozenset (`"news"`, `"academic"`, `"government"`, `"encyclopedia"`,
`"primary-text"`, `"book"`) — additive to `tier`: it describes what kind of source a domain is,
never how much to trust it.

### `DEFAULT_CONFIG`

A small, deliberately generic `AllowlistConfig` — a starting-point convenience, not a
comprehensive claim about source quality for any subject area or language. A real consuming
project should supply its own config sized to its own domain and editorial judgment.

Four general-interest domains with no primary repositories, which in practice reduces
`reputable_sources()` to "one general search filtered to Wikipedia and three wire services".
Its contents are frozen for back-compatibility: it is the default argument of
`ReputableSourceAllowlist.__init__`, so changing it would silently change behaviour for every
existing caller. Prefer a preset below.

### `SOFTWARE_CONFIG`, `RESEARCH_CONFIG`, `DATA_CONFIG`

Larger starting points for the three project archetypes, each with populated `tier_by_domain`,
`type_by_domain`, and `topic_primary_repos`.

The **same honesty ceiling applies to all three**, and is worth restating because a longer list
reads as more authoritative than a short one: these are *provenance* judgments — "this domain is
a defensible place to look" — never claims that a given page is correct, current, or unbiased. A
consuming project is expected to edit them, not inherit them uncritically. An official statistics
series, for instance, is authoritative about what it measured, which is not the same as being the
right series for a question.

### `config_for_project_type(project_type) -> AllowlistConfig`

Maps a `classify_project_type` value to its preset (`software`/`documentation` →
`SOFTWARE_CONFIG`, `research`/`writing` → `RESEARCH_CONFIG`, `data-pipeline` → `DATA_CONFIG`).
Unknown, `mixed`, and `unknown` types fall back to `DEFAULT_CONFIG` rather than guessing.

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

## `news` — perspective attribution for news-typed sources

> *Source: `agentteams/research/news.py`*

News is a contemporaneous account of *perspective* on an event — not verified fact, and not the
same epistemic class as an encyclopedia or government source. This module gives the `type="news"`
tag (already present in `reputable.py`'s `VALID_TYPES`, previously inert — stored and returned but
never read for behavior) its first real behavior: a consistent attribution string a caller can
present instead of stating a news claim as settled fact. It never adjudicates between outlets
reporting the same event differently.

### `PerspectiveKind`

`Literal["reported", "contested"]`. `"reported"` — a plain factual claim a news source is the
origin of (what happened). `"contested"` — a claim about how a source *characterized* something
(e.g. an outlet's editorializing description of a person or event), which deserves more hedging
than a bare factual report. This module doesn't decide which applies to a given claim — that
judgment needs the claim's own text — it only exports the shared vocabulary so callers across the
framework use the same two labels rather than independently-invented near-synonyms.

### `is_news_source(source) -> bool`

`True` when `source.type == "news"`. Exists so callers don't hardcode the string literal `"news"`
in more than one place.

### `perspective_attribution(source, published_at) -> str`

The single, shared place that formats a consistent attribution string for a news-typed source —
`"{domain} reported ({published_at})"`, or `"{domain} reported (date not available)"` when no date
was extractable. Degrades honestly rather than fabricating a date.

Publish-date extraction itself lives in `search.py`'s `extract_published_date` — a
content-parsing concern kept alongside that module's other HTML/PDF extraction, and separated here
specifically to avoid an import cycle (`search` → `news` → `reputable` → `search`, since
`reputable.py` already imports from `search.py`).

---

## `verify` — claim extraction and dual-lens fact verification

> *Source: `agentteams/research/verify.py`*

`extract_claims`, `audit_claims`, and `revise` below are all `async def` coroutines — call each
with `await`; every one of them awaits its own `chat_fn` call internally.

### `ChatFn`

A `Protocol` — the minimal chat-completion callable every function below takes as a parameter.
This module has no hardcoded model client; every function that needs an LLM call takes a
caller-supplied `ChatFn`. Its `__call__` returns `Awaitable[str]`, so the supplied callable must
itself be `async` (or otherwise return an awaitable). Supports an optional `want_json` hint the
callable may honor, but every function below stays correct even when it's ignored (see
`extract_claims`'s Behavior Notes).

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
