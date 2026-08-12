# Durable Canonical Agent Format — System Report

**Date:** 2026-08-10
**Plan:** `references/plans/durable-canonical-agent-format.plan.md`
**Steps:** `tmp/by-week/2026-W33/durable-canonical-agent-format.steps.csv` (46/46 complete)
**Suite state:** 4083 passed, 4 skipped, 2 deselected (documented pre-existing), 1 xfailed

---

## 1. What was built

The durable canonical agent format gives AgentTeamsModule a **durable,
system-agnostic, on-disk representation of an agent team** that any supported
framework can export to, hand-edit, and import back from — losslessly. It
hardens the existing Canonical Agent Interface (CAI) rather than creating a
parallel mechanism, and it preserves every prior mechanism (`convert.py`,
`bridge.py`, CAI interop `direct`/`bundle`).

Three layers now compose cleanly:

```text
framework files ──export──▶ CAI v2 dict ──materialize──▶ canonical directory
framework files ◀──import── CAI v2 dict ◀──load────────── canonical directory
```

1. **CAI v2** (`agentteams/interop.py`, `schema_version: "2.0"`) — the
   intermediate representation: instructions binding, agents (with
   capabilities, handoffs, invariant-core spans, optional raw front matter),
   skills, MCP servers, and framework-extension buckets.
2. **Canonical exploded format** (`agentteams/canonical.py`) — the durable
   on-disk form of that CAI dict.
3. **Six registered framework adapters** (`agentteams/frameworks/registry.py`)
   — every one a valid CAI interop source *and* target.

## 2. The canonical directory format

Default location: `<project>/.agentteams/canonical/` (constant
`DEFAULT_CANONICAL_SUBDIR`). Layout:

```text
team.cai.json                      # project-level CAI data
agents/<slug>.md                   # one file per agent (YAML front matter + body)
skills/<slug>/SKILL.md             # skill content (+ co-located files, verbatim)
references/**                      # non-agent reference content at recorded rel paths
```

Design rules:

- `team.cai.json` carries everything **except** the exploded keys `agents`,
  `skills`, `references` — no duplication.
- Agent files are human-editable Markdown. Front matter emits a fixed key
  order (`slug`, `name`, `description`, `source_path`, `capabilities`,
  `handoffs`, `raw_front_matter`) using a deliberately narrow YAML subset:
  JSON double-quoted strings, `true`/`false`, `json.dumps` numbers, flow
  lists for tool scopes, block lists for handoffs.
- Skills with captured files are written **verbatim**; hand-built entries
  without files get a synthesized readable `SKILL.md`.
- Captured invariant-core fences are appended to the body on materialize and
  re-lifted on load — fence content is preserved verbatim.

### Guarantees

| Guarantee | Mechanism | Pinned by |
|---|---|---|
| Round-trip fidelity | `load_canonical(materialize_canonical(export_to_cai(src))) == export_to_cai(src)` | `tests/test_canonical.py`, `test_dogfood_canonical.py` |
| Atomic writes | all writes via `atomicio._atomic_write_text` | K.2 audit + tests |
| Dry-run purity | planned writes collected, zero filesystem side effects (no `mkdir`) | `test_canonical.py` |
| Idempotent re-materialization | same input ⇒ same outputs | `test_canonical.py` |
| Path safety | slugs restricted to `^[A-Za-z0-9][A-Za-z0-9._-]*$`; reference paths reject absolute paths and `..` | `test_canonical.py` |
| No PyYAML runtime dependency | `_minimal_yaml_load` parses exactly the emitted subset; PyYAML preferred only when importable | fallback round-trip test with `__import__` monkeypatched |
| MCP security re-validation | canonical → framework import re-runs `mcp_emit._inert_problems()`; a hand-edited `team.cai.json` that weakens `security_review` **fails re-import** | `tests/test_canonical_mcp_revalidation.py` |
| Security preflight | canonical writes use the same interop freshness gate (`_assert_security_intelligence_fresh`); no bypass | G.2 audit |

## 3. Framework coverage

Registry order (`FRAMEWORK_IDS`): **copilot-vscode, copilot-cli, claude,
goose, agents-md, codex**. All six are valid CAI interop sources and targets.

- **goose** — agent sources are `.goose/recipes/<slug>.yaml`. A YAML-free
  parser (`frameworks/goose_recipe_read.py::parse_recipe_fields`) reads title,
  description, instructions, extensions, parameters, response, retry.
  `sub_recipes` become `send: true` delegation handoffs; `load("<slug>")`
  refs become `send: false` context-load handoffs. Recipe-level config
  round-trips via `cai["framework_extensions"]["goose"]` (union of builtin
  extensions, first-declared parameters/response/retry, replace-mode marker
  when `developer` is absent).
- **agents-md** — plain-Markdown `AGENTS.md` + `.agents/<slug>.md` emitter;
  now a valid interop target (still excluded from convert/bridge targets).
- **codex** — thin adapter extending agents-md rendering; generated notice
  documents Codex's nested-`AGENTS.md` walk. `.codex/config.toml` MCP
  emission is documented future work.
- **claude** — the only framework with a first-class skill concept
  (`.claude/skills/<slug>/SKILL.md`); skills round-trip through CAI.
- Capability vocabulary is the canonical seven tokens — `read, edit, search,
  execute, todo, agent, retrieval` — owned by `agentteams/capability_map.py`.

## 4. `canonical` — the interop-only pseudo-framework

`canonical` deliberately does **not** exist in `FRAMEWORKS` (plan §5.6). It
dispatches through two named guard clauses in `agentteams/interop.py`:

- **Export:** `framework == "canonical"` → `canonical.load_canonical(dir)`.
- **Import:** `target_framework == "canonical"` →
  `canonical.materialize_canonical(cai, dir)`.
- **Detection:** a directory containing `team.cai.json` is detected as
  `canonical` by `detect_framework()`.
- **Bundle refusal:** `run_interop()` rejects `--interop-mode bundle` with a
  canonical target — bundle artifacts would land inside the canonical
  directory and corrupt its `references/` tree on load.

CLI surface: `--framework canonical` and `--interop-source-framework canonical`
are accepted (`choices = FRAMEWORKS + ["canonical"]`), each requires
`--interop-from`; canonical is never a `convert`/`bridge` target. Default
canonical output resolves to `<project>/.agentteams/canonical/`.

```bash
# export any framework → canonical
agentteams --interop-from .github/agents --interop-source-framework copilot-vscode \
  --framework canonical --output .agentteams/canonical

# canonical → any framework
agentteams --interop-from .agentteams/canonical --interop-source-framework canonical \
  --framework claude --output .claude/agents
```

## 5. What was deliberately preserved

- `convert.py` path unchanged (coverage still narrower than interop).
- `bridge.py` lightweight bridges unchanged: `--bridge-check` /
  `--bridge-refresh` / `--bridge-merge` (fence-merge preserves content outside
  `AGENTTEAMS-BRIDGE` fences), artifacts under
  `references/bridges/<src>-to-<tgt>/`.
- CAI interop `direct` mode for framework↔framework transfer.
- Shared front-matter scanner `yaml_frontmatter.py` (Phase B) with
  byte-identity regression pins over `.github/agents/*.agent.md`.

## 6. Follow-up: bridges for cheap high-level abstraction from any system

**Status: not yet built.** The canonical implementation produced no new
universal bridge layer. Existing bridges already demonstrate the cheap
pattern, and the canonical format now makes it cheaper still.

### What exists today

Three bridges exist, all sourced from this repo's copilot-vscode team:

```text
references/bridges/copilot-vscode-to-claude/
references/bridges/copilot-vscode-to-copilot-cli/
references/bridges/copilot-vscode-to-goose/
```

Each carries `bridge-manifest.json` (SHA-256 source hashes for freshness),
`agent-inventory.md` (invokable/role table), `quickstart-snippet.md` (first
prompt), `entrypoint.md` (retrieval surface), `domain-boundary.md`. For goose
it also emits a `bridge-orchestrator.yaml` entry recipe. These bridges let a
target framework consume the team **without regenerating agent docs** — prose
artifacts plus fence-merged entry files. (This very goose session consumes one.)

### Proposed follow-up (phased, cheap-to-expensive)

1. **Canonical-rooted bridge sources (cheap).** Extend `--bridge-from` to
   accept a canonical directory (`team.cai.json` detection already exists).
   The canonical directory is framework-neutral and hand-editable, so bridges
   can source from it instead of a framework's native files — one source of
   truth for every bridge. Work: allow `canonical` in
   `--bridge-source-framework` and route `bridge_sources._collect_source_files`
   over `agents/*.md`.
2. **Bridge-any-target quickstart generator (cheap).** The bridge artifacts
   are mostly framework-agnostic prose. Add a minimal `generic` bridge flavor
   that emits only inventory + quickstart + entrypoint, letting a system with
   no native adapter depend on the AgentTeams abstraction purely through
   instruction text pointing at the canonical tree and orchestrator routing.
3. **Bridge-check over canonical (cheap).** Point `--bridge-check` hash
   verification at the canonical directory, giving every consumer framework a
   shared freshness signal instead of per-framework hash rows.
4. **Canonical as portable team package (moderate).** A zipped canonical
   directory plus a generated generic bridge is a portable "team tarball" any
   repo can drop in — full fidelity via `--interop-source-framework canonical`
   whenever the consumer later wants native rendering.
5. **Skip for now:** full native adapters for further frameworks; Codex
   `config.toml` MCP emission remains documented future work.

Acceptance for the follow-up: a fresh framework with **zero** adapter changes
can consume the team via bridge artifacts alone, and a framework with an
adapter can round-trip from the same canonical directory with
`test_dogfood_canonical`-level byte identity.

## 7. Known caveats

- `.claude/CLAUDE.md` bridge-managed fences still show the pre-F framework
  list; the durable data source (`.github/agents/_build-description.json`) is
  corrected and the next successful `--bridge-refresh` will propagate it
  (last merge invocation refused rc=2).
- Codex MCP emission: not built (documented in generated notice).
- Invariant-core re-import normalizes span position to end-of-body; fence
  order carries no merge semantics and content is preserved verbatim.
- Schema files were reformatted (not re-contented) by JSON round-trip during
  enum updates.

---

## Audit & revision notes (self-audit, 2026-08-10)

Audited against source before saving:

- Registry contents and order — verified against `frameworks/registry.py`.
- `canonical.py` constants, exploded keys, slug regex, PyYAML-free fallback,
  dry-run/no-mkdir contract — verified against module docstring and symbols
  (`TEAM_FILE_NAME`, `DEFAULT_CANONICAL_SUBDIR`, `_EXPLODED_KEYS`,
  `_SAFE_SLUG_RE`, `materialize_canonical`, `load_canonical`).
- Canonical dispatch guard clauses (`interop.py:108`, `interop.py:395`),
  `team.cai.json` detection (`interop.py:53`), bundle refusal
  (`interop.py:647`) — verified by grep.
- Bridge modes and artifact set — verified against `bridge.py` docstring and
  the three bridge directories on disk.
- Test totals (4083 passed) and the two documented pre-existing deselects —
  per Phase H/K close-out evidence in the steps CSV.

Revisions made during audit: (1) corrected an initial draft claim that
`canonical` was detectable via a directory name — detection is via the
`team.cai.json` marker file; (2) added the bundle-refusal rationale
(references-tree corruption) which the first draft omitted; (3) scoped the
follow-up section to acknowledge existing copilot-vscode→{claude,copilot-cli,
goose} bridges rather than implying no bridges exist.

---

## Addendum (2026-08-10): open items remediation

All items from §6 and §7 above were reviewed, planned, audited, and implemented
in a follow-up session — see `tmp/by-week/2026-W33/canonical-format-open-items-remediation.plan.md`
for full detail (findings, audit trail, revision log). Summary of disposition:

- **§7 caveat 1 (`.claude/CLAUDE.md` stale framework list) — fixed, and
  re-diagnosed.** The caveat's own diagnosis was wrong: `.claude/CLAUDE.md` is
  not a bridge target file at all (it has no `AGENTTEAMS-BRIDGE` fences), so no
  `--bridge-refresh`/`--bridge-merge` could ever have propagated the framework
  list into it. The actual mechanism is the generate/self-update pipeline
  (`--self --merge --framework claude`); the two prior attempts never used
  `--framework claude` and so never touched the file. Fixed: `_build-description.json`'s
  `project_goal` field corrected (its `deliverables` field already was), both
  merge directions re-run. A real root cause was also found for why the fix
  didn't fully apply automatically: the template's `output_conventions` line
  wraps `{DELIVERABLE_TYPE}` in backticks, and the merge tool's shrink-detector
  treats any edit to a backtick-wrapped span as data loss regardless of
  direction — logged to `references/agentteams-remediation-log.csv` since it
  will recur on the next `deliverables` change. The separately-recorded rc=2
  bridge-merge refusal (an unrelated, cosmetic bookkeeping issue — no content
  ever flowed through it) was closed out with a clean re-invocation.
- **§6 item 1 (canonical-rooted bridge sources) — implemented.** `canonical`
  is now a valid `--bridge-source-framework` value.
- **§6 item 2 (bridge-any-target quickstart generator) — implemented.**
  `generic` is now a valid bridge target (`--framework generic`, bridge-only).
- **§6 item 3 (bridge-check over canonical) — implemented**, folded into item
  1's work (hashing `team.cai.json` alongside `agents/*.md` closes the
  instructions/MCP/framework_extensions blind spot).
- **§6 item 4 (canonical as portable team package) — implemented.**
  `--package-team` zips a canonical directory plus its generic bridge into one
  portable archive.
- **§6 item 5 / §7 caveat 2 (Codex `config.toml` MCP emission) — implemented**
  (operator-approved, reversing the original "skip for now"). Verified against
  OpenAI's Codex MCP documentation via live research rather than assumed;
  applies Goose's stricter first-party/read-only auto-wire bar (not Claude's
  more permissive inert-write bar, and independently checked against Codex's
  own — more conservative — default sandbox/approval posture rather than
  assumed-by-analogy), since `.codex/config.toml` is a real, live config
  Codex reads to launch servers, unlike Claude's inert JSON sidecar. No
  comment-preserving TOML library exists to round-trip through, so the
  text-level splice is verified rather than assumed content-preserving: a
  post-splice check parses both pre- and post-splice text and refuses the
  write entirely if anything outside `[mcp_servers.*]` would change (closing
  a `@security` HALT found and independently re-verified resolved the same
  day — see `references/security-decisions.log.csv`).
- **§6 item 5's other half ("full native adapters for further frameworks")
  — formally re-affirmed as out of scope, not built.** This was already
  resolved by a separate, prior decision:
  `references/plans/non-goose-closeout-B-agents-md-emitter-2026-06-15.plan.md`
  chose to reach ~10 tools (Cursor, Cline, Windsurf, Aider, Zed, continue.dev,
  etc.) through the shared `agents-md` adapter rather than building dedicated
  adapters per tool. The only named, still-unbuilt "true multi-agent target"
  candidate is **Kilo Code** — never scoped, never committed to, not built
  here.
- **§7 caveats 3 and 4** (invariant-core span normalization; schema
  reformatting) — no action needed, both already documented as working-as-
  designed / cosmetic.

Test baseline note: this addendum's own session found the §-cited "4083
passed, 0 failed" baseline was itself stale by the time work began (true
baseline: 2 pre-existing failures, unrelated to this report's scope, caused by
already-uncommitted work from the same predecessor session this report
describes) — see the remediation plan's §5 for detail. A reader relying on
this report's original test-count claim after this addendum should prefer the
remediation plan's figures.
