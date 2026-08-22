# API Reference — AgentTeamsModule

Public API for the `agentteams` package. Each module corresponds to a stage in the pipeline or a support capability.

This reference defines the **supported public API surface** (documented modules and symbols). Modules and symbols not documented here are considered internal and may change without notice. Behavioral details may evolve between releases; check `CHANGELOG.md` for compatibility notes.

---

## Pipeline Modules

| Module | Role |
|--------|------|
| [`ingest`](ingest.md) | Load and normalize project description files |
| [`analyze`](analyze.md) | Classify project type, select archetypes (including contextual post-production selection), build team manifest |
| [`manifest_format`](manifest-format.md) | Manifest field derivation/formatting helpers (_format_*/_default_*/_collect_*) — carved from analyze |
| [`render`](render.md) | Resolve templates and produce rendered agent file content |
| [`emit`](emit.md) | Write rendered agent files to disk safely |
| [`fences`](fences.md) | Section-fencing internals (regexes, MergeResult, fenced merge, shrink detection) — carved from emit |

## Core Support Modules

| Module | Role |
|--------|------|
| [`convert`](convert.md) | Direct format migration between framework outputs |
| [`interop`](interop.md) | Canonical Agent Interface (CAI) interop pipeline |
| [`bridge`](bridge.md) | Lightweight runtime compatibility bridge artifacts |
| [`team_package`](team-package.md) | Portable team package: a durable canonical directory plus its generic bridge, zipped for a repo with zero `agentteams` integration |
| [`cli`](cli.md) | CLI package decomposition — which module owns which stage (flags live in the CLI Reference) |
| [`output-plan`](output-plan.md) | Plans which files a manifest produces, before anything renders |
| [`update-report`](update-report.md) | `update.report.md` — durable record of an `--update` run's decisions |
| [`fleet`](fleet.md) | Multi-workspace `--update --merge` (`--fleet DIR`) with git snapshot + diff audit |
| [`drift`](drift.md) | Detect template-to-instance drift for incremental updates |
| [`behavioral-drift`](behavioral-drift.md) | Detect behavioral divergence in agent runs vs. specification |

## Audit & Security Support

| Module | Role |
|--------|------|
| [`scan`](scan.md) | Proactive security scan for generated agent files |
| [`redteam`](redteam.md) | The `agentteams/redteam/` package — audit internals + corpus coverage/density (F2). The model-scoring + attack-generation *scripts* are in the [Red-Team Model Scoring & Attack Generation](../redteam-model-scoring-guide.md) guide |
| [`session_scan`](session_scan.md) | Repo at-large issue scan (CHANGELOG Known Issues, plan-steps pending/blocked, git status) for orchestrator closeout |
| [`audit`](audit.md) | Post-generation static and AI-powered audit |
| [`integrity`](integrity.md) | Hash manifest over the modules that enforce the constitution itself |
| [`feature_audit`](feature-audit.md) | Verifies features documented in Feature Inventory still function, via the machine-readable feature registry |
| [`living-doc`](living-doc.md) | Living-document conformance (Rule 7) over unfenced agent prose |
| [`remediate`](remediate.md) | Auto-correct audit findings via standalone Copilot CLI |
| [`security-refs`](security-refs.md) | Build live security intelligence placeholders for templates |
| [`framework-research`](framework-research.md) | Detect upstream framework drift; transmit via `--update --merge`; supervised-PR auto-update path |

## Enhancement & Enrichment

| Module | Role |
|--------|------|
| [`enrich`](enrich.md) | Default-value audit and context-aware placeholder enrichment |
| [`memory-index`](memory-index.md) | Lexical (BM25) search index for work summaries and documentation |
| [`memory-index-incremental`](memory-index-incremental.md) | In-place single-document index patching; declines conservatively to a full rebuild |
| [`fence-inject`](fence-inject.md) | Inject and extract fenced-region content from agent files |
| [`code-index`](code-index.md) | Code & API index over repository scripts and the external APIs they use (gitignored local cache) |
| [`code-sources`](code-sources.md) | Source resolution and partitioning for the code & API index |

## Agent & Team Analysis

| Module | Role |
|--------|------|
| [`graph`](graph.md) | Directed graph inference for agent team topology |
| [`architecture`](architecture.md) | Module-dependency map of a repository's own Python package, built from its imports |
| [`git-hooks`](git-hooks.md) | Commit-triggered refresh of the topology and architecture maps (`--install-git-hooks`) |
| [`model-routing`](model-routing.md) | Framework-neutral model-routing contracts for cost/capability tiering |
| [`eval_suite`](eval-suite.md) | Build behavioral evaluation specs for agent team runs |
| [`eval-adapters`](eval-adapters.md) | Convert neutral eval-suite contracts into Inspect AI and OpenAI Evals artifacts |

## Manifest & Documentation

| Module | Role |
|--------|------|
| [`frameworks`](frameworks.md) | Per-framework adapter classes |
| [`man`](man.md) | Generate and validate the project man-page source |
| [`handoff_payloads`](handoff_payloads.md) | Typed handoff payload substrate for plan `.steps.csv` artifacts |
| [`plan_steps`](plan_steps.md) | Tolerant reader for plan `.steps.csv` artifacts |
| [`plan_steps_todo`](plan-steps-todo.md) | TodoWrite projection of plan `.steps.csv` (CSV is canonical; TodoWrite is the projection) |
| [`liaison_logs`](liaison-logs.md) | Cross-repository coordination logs and artifacts |
| [`parallel_plan`](parallel-plan.md) | Parallelisation analysis over a plan's `depends_on` column |
| [`feature_inventory`](feature-inventory.md) | Generated inventory of the shipped feature surface |
| [`front_matter_reconcile`](front-matter-reconcile.md) | Report (and optionally apply) template-vs-deployed YAML front-matter divergence |
| [`template_pins`](template-pins.md) | Trust root for installed template digests (`--pin-templates`) |

## Host Features & Bridge Emission

| Module | Role |
|--------|------|
| [`host_features`](host-features.md) | Parse / validate `<ns>:<feature>` opt-in subselector tokens for emission gating |
| [Workspace Privilege Scoping](workspace-privilege-scoping.md) | Opt-in `privilege_profile` / `claude:sandbox` — emit Claude Code's OS-level write-confinement sandbox (feature page) |
| [`baseline`](baseline.md) | Deterministic SHA-256 emission baselines (capture / diff) used by regression tests |
| [`bridge_subagents`](bridge-subagents.md) | Per-agent Claude subagent stub emitter (bridge:copilot-vscode-to-claude:subagents) |
| [`bridge_subagents_goose`](bridge-subagents-goose.md) | Per-agent Goose stub-recipe emitter (bridge:`<src>`-to-goose:subagents) |
| [`bridge_sources`](bridge-sources.md) | Source-team inventory, file collection, hashing + bridge-freshness check (framework-aware) |
| [`hooks_emit`](hooks-emit.md) | Claude hooks settings + recursion-bounded guard emitter (bridge:copilot-vscode-to-claude:hooks) |
| [`instructions_split`](instructions-split.md) | Cache-aware CLAUDE.md layout: preamble + boundary + dynamic stanza (bridge:copilot-vscode-to-claude:cache-split) |
| [`schedule_emit`](schedule-emit.md) | `/schedule` routine spec emitter (bridge:copilot-vscode-to-claude:schedule) |
| [`goose_config`](goose-config.md) | Locate + safely mutate Goose's `config.yaml` for source/model switching (no key handling) |
| [`mcp_detect`](mcp-detect.md) | Detect MCP server configuration available to the target host |
| [`mcp_emit`](mcp-emit.md) | Emit MCP server wiring into generated teams |
| [`codex_mcp_emit`](codex-mcp-emit.md) | MCP server emission into Codex's `.codex/config.toml` |
| [`toml_write`](toml-write.md) | Minimal hand-rolled TOML serializer bounded to what Codex's MCP config tables need |
| [`bridge_pair_docs`](bridge-pair-docs.md) | Prose renderers for the bridge's framework-agnostic pair-dir artifacts (carved out of `bridge`) |
| [`bridge_skills`](bridge-skills.md) | Claude skill bodies emitted by the bridge (carved out of `bridge`) |

## Multi-Framework Pinned Sync

| Module | Role |
|--------|------|
| [`multi_sync`](multi-sync.md) | Pinned-sync orchestrator: keeps every agentic interface in sync through the canonical hub, pin authoritative on conflict |
| [`sync_pin`](sync-pin.md) | The pinned-source contract for multi-framework sync |
| [`sync_baseline`](sync-baseline.md) | Real-content baseline snapshot writer for native ↔ canonical synchronization |
| [`sync_classifier`](sync-classifier.md) | Generalized three-way classifier for native ↔ canonical synchronization |
| [`canonical`](canonical.md) | Durable exploded on-disk canonical agent format — the hub `multi_sync` reconciles through |
| [`capability_map`](capability-map.md) | Canonical tool-scope vocabulary and framework ↔ canonical capability mapping, shared by `interop` and this family |

## PR Management

| Module | Role |
|--------|------|
| [`pr_management`](pr-management.md) | Recipient registry, gh-CLI wrappers, stale-PR scan, end-of-task three-way disposition prompt |
| [`research`](research.md) | Optional `agentteams[research]` extra: search, reputability scoring, claim verification |

---

## Typical Pipeline Usage

```python
from pathlib import Path
from agentteams import ingest, analyze, render, emit
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter

description = ingest.load("brief.json")
manifest = analyze.build_manifest(description, framework="copilot-vscode")
rendered = render.render_all(manifest, templates_dir=Path("templates"))

# render_all() returns framework-agnostic content; adapters add framework-specific
# wrappers and metadata before emit.
adapter = CopilotVSCodeAdapter()
final = [(p, adapter.render_agent_file(c, Path(p).stem, manifest))
         for p, c in rendered]

result = emit.emit_all(final, output_dir=Path(".github/agents"), dry_run=False)
emit.print_summary(result, manifest)
```

`render.render_all()` does not apply framework-specific post-processing on its own. Use the appropriate adapter from `agentteams.frameworks` to convert rendered content into the final framework format before passing it to `emit.emit_all()`.

---

## Interoperability API Family

The interoperability feature family has four approaches, each a dedicated module or module
group:

1. [`convert`](convert.md) for format migration.
2. [`interop`](interop.md) for CAI normalization and transfer.
3. [`bridge`](bridge.md) for lightweight, one-directional source-canonical runtime bridging.
4. [Multi-Framework Pinned Sync](#multi-framework-pinned-sync) (`multi_sync` and peers) for
   continuous, bidirectional multi-framework consistency through a canonical hub, rather than a
   one-directional mirror of a single source.

Bridge and pinned-sync solve related but different problems and are not layered on each other —
pick bridge for a lightweight, one-directional mirror of one canonical source into another
framework's directory (re-running to pick up source changes is a normal, supported workflow —
see `bridge.md`'s own Mode Selection Guidance); pick pinned-sync when every framework is a peer
that needs to stay reconciled as any of them changes. For bridge's own workflow-level usage and
mode selection, see the [Interoperability](../interoperability.md) page (which does not yet
cover pinned-sync).

---

## Coverage gaps

This index does not cover every module. **20 top-level public modules have no API
page**, listed here rather than left to be discovered:

`advisory`, `ai_bad_habits`, `atomicio`, `audit_agent_contract`, `audit_types`,
`backup`, `budget`, `capability_hints`, `errors`, `front_matter_merge`,
`graph_inputs`, `recipe_fields`, `security_feed_render`, `stale_detector`,
`stale_remediate`, `svg_render`, `tool_metadata_catalog`, `unfenced`,
`vscode_tasks`, `yaml_frontmatter`.

The count is a **ratchet, not a target**: `tests/test_module_doc_ratchet.py` fails if a
new module arrives without a page, and fails equally if a listed module gains one and is
not removed from the baseline. It arrived falling (22 → 20) rather than merely frozen.
Most of the growth came from CH-07 carves splitting documented modules — `unfenced` out of
`fences`, `audit_types` out of `audit`, `graph_inputs` out of `graph`.

Four exceed 400 lines (`stale_detector` 739, `vscode_tasks` 673, `backup` 486,
`svg_render` 470) and are the highest-value additions. `backup`'s public API is
partially documented inside [`emit`](emit.md), which re-exports it.

They are listed rather than written because a thin page that misdescribes a module is
worse than a missing one — a reader trusts what is written. The pages added on
2026-07-29 (`living-doc`, `update-report`, `output-plan`,
`memory-index-incremental`, `cli`) were scoped to modules whose behaviour was
established first-hand rather than inferred.

**What *is* mechanically guaranteed:** every documented signature matches the code, and
the feature summary's total equals its addends —
`tests/test_api_doc_signatures.py`. Coverage is a known gap; accuracy is enforced.
