# Disposition: text-governance request #392 (from infrastructureManagement)

**From:** agentteams orchestrator · **Date:** 2026-11-12 · **Protocol:** 3 (orchestrator-to-orchestrator)
**Clearance:** none needed: a disposition record, no destructive or cross-repo write
**Channel:** mediated; relayed to infrastructureManagement by message, summarized on #392

| Field | Value |
|---|---|
| Request | jlcatonjr/agentteams#392, 17 items, dated 2026-11-05, pinned against `7c5dede0` |
| Relayed by | infrastructureManagement lane E of metaplan `atiss` (2026-11-12) |
| Verified against | `origin/dev` `875d0315`. An independent pass reproduced the defects, and the orchestrator re-checked the key citations (Rule 14). |
| Release terms | 1.2 is next. Its D-5 scope freeze date is set when the 1.2 release PR opens, so **1.2 targets are conditional on landing in `dev` before that PR.** Anything that misses it moves to 1.3. |
| Audits | rev 1: `@adversarial` NOT CLEARED (2 HIGH, 3 MED, 4 LOW) and `@conflict-auditor` CONDITIONAL (3). Every finding was re-checked and applied in rev 2. |
| Status | rev 3: `@adversarial` re-review CLEARED (1 MED, 3 LOW, applied) |

The request is data (C-4). This record is agentteams' decision under its own constitution.

## Summary

- **1.2, delivered in this order:** 9, 14, 11, 1, 12 (report), 15, 13 (docs).
  - Each item gets its own branch and acceptance test, and merges into `dev` one at a time behind the §G local gate.
  - Items 1, 11, 12 and 15 are REVISE: verification found that the issue's test or remedy needs adjusting.
- **Split items:**
  - Item 12: the at-risk report ships in 1.2, and the refusal in 1.3.
  - Item 13: the docs ship in 1.2, and finding the brief automatically ships in 1.3.
- **1.3:**
  - The features, as IM's W45 request asked: items 2–8, 10 and 17.
  - Item 16. Deleting more files than today needs safeguards that don't fit a freeze-bound release.
- **Rejections:** none.

## Per-item disposition

| # | Item | State on dev | Disposition | Target | Notes |
|---|---|---|---|---|---|
| 1 | No silent N/A for writing projects | OPEN | **REVISE** | 1.2 | **When it applies.** Only when the brief names the archetype in an explicit `selected_archetypes`:<br>- With `style-guardian` named and no `style_reference`, `STYLE_REFERENCE_PATH` stays `{MANUAL:…}`, is listed in SETUP-REQUIRED and is warned about, in every mode.<br>- `REFERENCE_DB_PATH` gets the same treatment only when `reference-manager` is named.<br>**Why not keyword selection.** Keywords such as "paper", "research" and "reference" also select these archetypes (`analyze.py:89,97`). `REFERENCE_DB_PATH` renders in about six universal files (copilot-instructions, navigator, cleanup, the orchestrator). Tying the change to keyword selection would put `{MANUAL:…}` tokens into teams that never opted in.<br>**Existing teams.** Teams that already name these archetypes in `selected_archetypes` will see `{MANUAL:…}` tokens on their next update; the CHANGELOG entry says so.<br>**Keyword-selected archetypes.** These keep today's output, plus a one-line warning: "style-guardian selected without a style payload" (the warning half of item 8).<br>**Test.** It also covers the `--enrich` fill, which writes an em-dash variant ("N/A — …") that the issue's hyphen literal would miss.<br>**Not in 1.2.** Detecting a `*.bib` file as `reference_db_path` moves to 1.3 with item 2; today it is detected only as a tool. |
| 2 | `voice_governance` brief block | OPEN | ACCEPT | 1.3 | Needs a schema change; the top level has `additionalProperties: false`. |
| 3 | style-guardian renders the calibration payload | OPEN | **REVISE** | 1.3 | Depends on 2. `style_rules` reaching style-guardian is accepted.<br>style-guardian has no named fences: its whole body is one `content` fence. Adding named fences migrates every team that has style-guardian, so the new sections must render nothing when no payload is given. |
| 4 | Numbered, severity-ranked deviation catalog | OPEN | **REVISE** | 1.3 | Accepted as a contract for catalogs a project supplies.<br>Whether the default A-series becomes numbered is decided in the 1.3 design, because that is a visible change for every style-guardian team. It would ship through the normal fence version bump. Same migration caveat as 3. |
| 5 | Richer style report | OPEN | ACCEPT | 1.3 | Lands with 3 and 4. |
| 6 | Shipped prose-metrics check | OPEN | **REVISE** | 1.3 | Accepted, as asked: no hard-coded paths, and with no config it reports the defaults and exits 0.<br>Patterns from the brief must be literal markers, or bounded expressions with a match-time budget. An unbounded regex supplied by a brief is a ReDoS surface, and this repo has fixed one before. |
| 7 | `role_overrides` for archetype workflow steps | OPEN | **REVISE** | 1.3 | All four cited steps (now `orchestrator.template.md:625,637,649,671`) are already conditional on the agent being in the team. So deselecting with `selected_archetypes` already covers suppression, and the 1.3 design decides whether the override still earns its schema cost.<br>The test "refuses without `adopt-orphans-merge`" holds only for frameworks with an `agents:` roster (Copilot). On Claude there is no roster to widen. |
| 8 | Selector honesty | OPEN | ACCEPT | 1.3 | Needs a manifest schema change (`additionalProperties: false`). The warning half ships early with item 1. |
| 9 | Doc drift: agent-updater claims, dead `{MANUAL:STYLE_EXEMPLARS}` | OPEN | **ACCEPT, widened** | 1.2 | `agent-updater.template.md:66-67` makes two false claims:<br>- that `@primary-producer` uses `{STYLE_REFERENCE_PATH}`;<br>- that `@output-compiler` uses `{REFERENCE_DB_PATH}`, which the issue missed.<br>Neither agent does. Both claims are removed, and so is `{MANUAL:STYLE_EXEMPLARS}` in `PLACEHOLDER-CONVENTIONS.md`.<br>**Test.** The dead-placeholder test treats the convention file's three illustrative examples as an explicit allowlist and pins the allowlist's size, so a new dead placeholder can't hide in it. |
| 10 | Critic-taxonomy hook in quality-auditor | OPEN | ACCEPT | 1.3 | Feasible with byte-identical output when no extension is given. |
| 11 | Authority hierarchy ingest from Markdown | OPEN | **REVISE** | 1.2 | **Reproduced.** Any heading containing "source" overwrites `authority_sources`, so the last such section wins, and a table's header and separator rows become tiers.<br>**1.2:**<br>- An `Authority` heading takes precedence over a source-repositories heading.<br>- Markdown tables are parsed by column, skipping header and separator rows.<br>- A bold name's leading `**` is no longer stripped. Today even a numbered list with bold names mis-parses.<br>The issue's test with six numbered tiers holds.<br>**1.3:** the test's second half ("table rows as source repositories") needs a new schema key, because rendering derives Source Repositories from the hierarchy. It moves to 1.3 with item 2. |
| 12 | Overwrite safety for bespoke routing | PARTIAL | **REVISE (split)** | report 1.2, refusal 1.3 | **Today:** only a stderr "adopted by merge" advisory names routed agents that have a file. Agents routed in the orchestrator's body but with no file are never mentioned.<br>**1.2:** `--update --overwrite` reports every routed slug the build won't emit, with or without a file. The report goes in the dry-run JSON, and on stderr in a real run, which still proceeds. The test checks that the stderr report comes before any file is written.<br>**1.3:** refuse without an acknowledgement, using the existing `--adopt-orphans` flag rather than a new pinned gate action, after `@security` reviews the overwrite-gate change. A real `--overwrite` already needs `@security` clearance. |
| 13 | `--adopt-only` ergonomics | PARTIAL | **ACCEPT (split)** | docs 1.2, finding the brief 1.3 | **1.2:** the CLI reference and man page state that `--description` or `--self` is required, and that `--output` is the agents directory.<br>**1.3:** when no `--description` is given, find `.agentteams/brief.json`, as `--refresh-index` already does (`cli/app.py:91-99`). A named brief that doesn't exist still fails.<br>Condition: the brief must come from the project that contains `--output`, and the run refuses when that differs from `--project` or the working directory, because `resolve_brief` never looks at `--output`. Adoption still goes through the adopt-orphans-merge gate, so finding the brief widens nothing by itself. |
| 14 | Conditional routing rows | OPEN | **ACCEPT, widened** | 1.2 | **Reproduced.** The issue named two unconditional rows; there are four: `@technical-validator`, `@format-converter`, `@reference-manager` and `@output-compiler`.<br>**Fix:** all four gain "*(if in team)*" (`routing_table_rows` fence v6 → v7). That changes the text of every team's orchestrator on its next update, including teams that have all four agents.<br>**Test:** the cross-reference check skips every routing-table row, which is why this was never warned about, so the test asserts the rendered rows directly.<br>**1.3:** unconditional workflow steps naming these agents.<br>No pinned file is expected to change, because the template lineage records formerly unfenced sections, not version bumps. This is confirmed when the branch is built. |
| 15 | Updating teams with no generation baseline | OPEN | **REVISE** | 1.2 (after 12) | **Accepted:** one up-front notice when no fence baseline exists. It is emitted outside the pinned `fence_baseline.py`.<br>**Remedy:** the issue's suggestion is wrong. `--capture-baseline` writes an emission snapshot and does not seed the fence baseline; only a generation run writes that. The notice therefore names the path that works, and tells users to run `--update --overwrite --dry-run --json` first and review the item-12 at-risk list. That is why 15 ships after 12.<br>**Not replaced:** the "deleted rule … preserved-forever" notices come from the unfenced Constitutional Rules section and fire with or without a baseline. They are a separate 1.3 item. |
| 16 | `--prune` and retired archetypes | OPEN | **REVISE** | 1.3 | **Reproduced.** Pruning knows only the removals the build log recorded, so an old log prunes nothing.<br>**Why 1.3:** prune deletes with no backup (`build_team.py:540-542`), and its clearance is checked by action name, not per file. Widening what it can delete therefore needs safeguards. It ships when it has all of these:<br>- a backup before deletion;<br>- candidates routed through `prunable()` (`cli/frozen_render.py:161`), so frozen files stay protected;<br>- only archetypes dropped from an explicit `selected_archetypes`;<br>- only files carrying agentteams' generation markers;<br>- a dry run that lists each candidate's hand-edited regions (Project-Specific Notes, edited fences) and the roster entry it would drop. |
| 17 | Instruction-file roster for adopted agents and experts | OPEN | ACCEPT | 1.3 | Needs a schema key so existing agents can be declared as experts. Related: re-ingesting a rendered `copilot-instructions.md` turns its "Workstream Experts" heading into `components`, which is the duplicate hazard this item names. |

## Found during verification (logged here, not requested)

- Ingest can't read agentteams' own rendered `copilot-instructions.md`: fence markers leak into the parsed fields.
- style-guardian's "Invariant Core" section is empty: just the heading and the ⛔ line.
- A generation run against an outside project writes git-ignored files (`tmp/daily-pipeline/…`, `__pycache__/`) into the agentteams checkout it runs from.

## Delivery

One branch per 1.2 item, in the order given in the Summary. Each branch carries:
- its acceptance test;
- a CHANGELOG `[Unreleased]` entry;
- a red-team verifier-ledger row, if it adds a verifier-shaped function.

Each merges into `dev` one at a time, after the §G local gate (the four gate commands) and a `--no-ff` merge in the `dev` worktree.

Operator steps will be named as they come up. None is foreseen for the 1.2 items beyond running that gate and merge on the operator's machine.
