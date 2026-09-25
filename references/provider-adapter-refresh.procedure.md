# Provider-Adapter Refresh Procedure

**Status:** active · **Owner:** `@framework-adapters-expert` (detects + applies adapter edits),
`@orchestrator` (routes), `@security` (clears any workflow/cross-repo change) · **Established:** 2026-09-22

**Origin:** the 2026-09-22 provider-adapter freshness assessment,
`references/plans/2026-09-22-provider-adapter-freshness-assessment.report.md`. That report found
the watcher fetched six providers' docs but only *diffed* Claude, scanned raw HTML with a
false-signal-prone regex, and had **no path from "provider changed" to "adapter updated."** This
procedure is the part that fixes tomorrow: it names the per-provider work and routes it.

**Trigger:** `.github/workflows/framework-auto-update.yml` (daily cron `23 7 * * *`)
**Detector:** `agentteams/framework_research.py` (`refresh_snapshot` → `propose_module_patch`)
**Single source of truth:** `agentteams/frameworks/format_spec.py` (`FORMAT_SPECS`)
**Tests:** `tests/test_framework_research.py`, `tests/test_format_spec_single_source.py`

---

## 1. Why this exists (and what it deliberately does NOT do)

The freshness watcher **detects and proposes; it never edits adapter code.** This is not a gap to
close — it is the design. Fetched vendor docs are inert data (Constitutional C-4), and writing an
adapter's `tools:`/front-matter/model constants unattended would be a privilege change. So the
automated loop stops at a **doc-note proposal PR**, and a human — `@framework-adapters-expert` —
decides whether a real drift signal warrants an adapter edit.

What this procedure adds on top of the watcher:

- It **names the adapter edit sites per provider**, so "Copilot changed its front matter" becomes
  a specific edit against a specific file, not a scavenger hunt.
- It **defines the freshness contract** — how often each provider is re-verified and what a stale
  or relocated doc looks like — so the work is not gated on someone remembering.

The prior single-provider system this generalizes (`CoPilotAgentDocumentation`) failed on exactly
one point: it relied on an LLM agent to *notice* staleness and had no mechanical enforcement, so
several docs sat stale for 9–11 cycles. The lesson is baked in here: **freshness is enforced by a
test, not by attention** (see §4).

---

## 2. The single source of truth

`agentteams/frameworks/format_spec.py` holds one `FormatSpec` per provider carrying BOTH:

- the **emit contract** — `emitted_front_matter_keys`, and the canonical
  `COPILOT_INSTRUCTIONS_FILENAME` / `AGENT_FILE_EXTENSION` constants; and
- the **upstream-watch contract** — `source_url`, `expert_ref`, `expected_doc_tokens`,
  `expected_locations`.

`framework_research.FRAMEWORK_REGISTRY` is **derived** from `FORMAT_SPECS` — there is no second
hand-maintained copy to drift. `tests/test_format_spec_single_source.py` fails the build if a
spec's `emitted_front_matter_keys` disagrees with the live adapter, or if the registry stops
agreeing with the specs, or if the watcher's `claude.py` constant-name regex stops matching.

> **When a provider format changes, `format_spec.py` is the first file you edit** — then the
> adapter, then re-run the tests below. The two can no longer diverge silently.

---

## 3. The detector and its fetch-integrity states

`refresh_snapshot` fetches each provider's `source_url`, strips HTML to text, scans for the
provider's `expected_doc_tokens` / `expected_locations`, and computes a **per-framework**
`keys_diff` (all six, not just Claude). Each `frameworks[id]` entry carries a `fetch_status`:

| `fetch_status` | Meaning | What it must NOT be read as |
|---|---|---|
| `ok` | Real page scanned; `keys_diff` is meaningful | — |
| `moved` | Redirected to a **different host** — the doc relocated | "no drift" (this is the silent-false-negative the assessment flagged) |
| `empty` | Resolved but page text < 500 bytes (stub/SPA shell/error) | "no drift" |
| `failed` | Network error (`fetch_error` carries it) | "no drift" |
| `skipped` | Offline run | anything |

A `moved` or `empty` status is a **finding that a `source_url` needs re-checking**, surfaced
inline in the rendered table (`fetch → final_url`). Update the URL in `format_spec.py` and, if the
provider genuinely restructured, proceed to §5.

Run it by hand:

```bash
python3 scripts/research_claude_code_docs.py            # ONLINE fetch — writes latest.json snapshot
python3 scripts/research_claude_code_docs.py --propose  # build the doc-note proposal FROM the snapshot (no adapter writes)
python3 scripts/research_claude_code_docs.py --freshness-view   # unified view (reads snapshot + register; no fetch)
```

`--propose` and `--freshness-view` read the cached snapshot; only the bare invocation fetches.

The snapshot is gitignored operator-local state (`tmp/daily-pipeline/framework-research/latest.json`).

---

## 4. The freshness contract (mechanically enforced)

Each provider's doc URL is re-fetched daily by the cron. But a *reachable* URL is not a *verified*
one — the token scan confirms structure, not that a human read the page. The manual verification
register `references/agent-provider-docs.reference.md` carries a `last_verified` + `window_days`
per provider and **fails the test suite when an entry is past its window**. That test is the
enforcement the prior system lacked; do not defeat it by bumping the date without opening the URL.

**Reconciliation (Phase 3):** the automated watcher (`fetch_status` + `keys_diff`, per run) and the
manual register (`last_verified`, per human check) are two views of the same six providers. The
unified freshness view answers, in one place: *for each provider — when was it last fetched, what
was its drift, and when did a human last verify it.*

---

## 5. The procedure — provider changed → adapter updated

Two stages. Stage 1 is mechanical; Stage 2 is the human-gated adapter edit.

### Stage 1 — Mechanical (scripted, no judgment)

```bash
# 1.1  Refresh the snapshot (ONLINE fetch), then read the per-framework drift + fetch integrity
python3 scripts/research_claude_code_docs.py            # fetches; writes latest.json
python3 scripts/research_claude_code_docs.py --propose  # proposal FROM the just-written snapshot

# 1.2  Any framework showing fetch_status moved/empty → its source_url is stale.
#      Any framework with keys_diff.missing_upstream (a token we rely on vanished
#      from the doc) or new_upstream (a token the doc added that we don't emit)
#      is a candidate for an adapter change — NOT proof of one.

# 1.3  Confirm the single source of truth still agrees with the adapters
python3 -m pytest tests/test_format_spec_single_source.py tests/test_framework_research.py -q
```

### Stage 2 — Authored adapter edit (judgment required, routed to `@framework-adapters-expert`)

A drift signal is a candidate, not a verdict (the token scan cannot tell a *required* key from a
merely *documented* one). When `@framework-adapters-expert` confirms a real format change, the
edit sites are:

| Provider changed… | Edit here (format_spec.py FIRST, then the adapter) | Then run |
|---|---|---|
| Front-matter keys (any provider) | `format_spec.py` `emitted_front_matter_keys` + the adapter's `required_front_matter_keys()` (+ copilot's `_REQUIRED_YAML_KEYS`/`_YAML_DEFAULTS`) | `test_frameworks.py`, `test_format_spec_single_source.py` |
| Doc URL moved (`fetch_status: moved`) | `format_spec.py` `source_url` | `test_framework_research.py` |
| Watch tokens / locations | `format_spec.py` `expected_doc_tokens` / `expected_locations` | `test_format_spec_single_source.py` |
| Agent file extension | `format_spec.py` `AGENT_FILE_EXTENSION` (used by the Copilot adapter) | `test_frameworks.py` |
| Instructions filename | `format_spec.py` `COPILOT_INSTRUCTIONS_FILENAME` (canonical); note matcher sites still hold the literal — grep `copilot-instructions.md` if the rename must propagate | full framework suite |
| Claude tool-name mapping | `claude.py` `_VSCODE_TO_CLAUDE_TOOLS` | `test_framework_capability_key_contract.py` |
| Goose recipe format/version | `goose.py` `_RECIPE_VERSION` + hand-emitted YAML | `test_frameworks.py::TestGooseAdapter` |
| Codex config.toml MCP schema | `agentteams/codex_mcp_emit.py` (top-level, not under `frameworks/`) | `test_codex_mcp_emit.py` |

The adapter change ships as an ordinary reviewed PR. The daily `framework-auto-update.yml` PR only
ever touches `references/*-expert.md` observation stanzas — never `agentteams/frameworks/*.py`.

### Stage 3 — Close out

1. `CHANGELOG.md` entry (`changelog-link.yml` requires one for any PR touching `agentteams/**/*.py`).
2. `@conflict-auditor` — Constitutional Rule 8, after any multi-file change.
3. If the drift revealed a gap in the *tool* rather than an adapter, log it to
   `references/agentteams-remediation-log.csv` (Rule 11).
4. Update `references/agent-provider-docs.reference.md` `last_verified` for the provider you checked.

---

## 6. What this procedure does *not* cover

- **It does not auto-edit adapters.** By design (C-4). The detector proposes; a human applies.
- **It does not verify provider *behavior*.** Whether a host actually honors a scoped tool grant
  or a front-matter key is a live-model question (`AGENTTEAMS_LIVE_MODEL_TESTS`), scheduled
  separately (Phase 3). The doc scan sees documentation, not runtime.
- **It does not detect a token the scan was never told to watch.** `expected_doc_tokens` is a small
  allow-list; a genuinely novel provider concept appears as `new_upstream` only if it collides with
  an existing token. Broadening the watch vocabulary is itself an `@framework-adapters-expert` edit
  to `format_spec.py`.
- **For JS-rendered (SPA) provider docs, "trustworthy" means fail-loud, not drift-detecting.**
  Some providers (e.g. `codex` → `learn.chatgpt.com`, possibly `goose-docs.ai`) serve docs whose
  server-side HTML strips to little text. Such a page returns `fetch_status: empty` on every run —
  the token scan cannot observe drift there, but it also cannot report a false "no drift": `empty`
  is surfaced, not swallowed. `agents_md` is `expected_doc_tokens=()` by design (the standard has no
  schema). So across the six, the *token-drift* guarantee is real only for the frameworks whose docs
  are server-rendered; for the rest the guarantee is reachability + relocation detection. Genuine
  drift on an SPA-served provider needs the Phase-3 live-behavior check or a human read, not this scan.
