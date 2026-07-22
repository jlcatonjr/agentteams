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
| [`fleet`](fleet.md) | Multi-workspace `--update --merge` (`--fleet DIR`) with git snapshot + diff audit |
| [`drift`](drift.md) | Detect template-to-instance drift for incremental updates |
| [`behavioral-drift`](behavioral-drift.md) | Detect behavioral divergence in agent runs vs. specification |

## Audit & Security Support

| Module | Role |
|--------|------|
| [`scan`](scan.md) | Proactive security scan for generated agent files |
| [`session_scan`](session_scan.md) | Repo at-large issue scan (CHANGELOG Known Issues, plan-steps pending/blocked, git status) for orchestrator closeout |
| [`audit`](audit.md) | Post-generation static and AI-powered audit |
| [`remediate`](remediate.md) | Auto-correct audit findings via standalone Copilot CLI |
| [`security-refs`](security-refs.md) | Build live security intelligence placeholders for templates |
| [`framework-research`](framework-research.md) | Detect upstream framework drift; transmit via `--update --merge`; supervised-PR auto-update path |

## Enhancement & Enrichment

| Module | Role |
|--------|------|
| [`enrich`](enrich.md) | Default-value audit and context-aware placeholder enrichment |
| [`memory-index`](memory-index.md) | Lexical (BM25) search index for work summaries and documentation |
| [`fence-inject`](fence-inject.md) | Inject and extract fenced-region content from agent files |

## Agent & Team Analysis

| Module | Role |
|--------|------|
| [`graph`](graph.md) | Directed graph inference for agent team topology |
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

## Host Features & Bridge Emission

| Module | Role |
|--------|------|
| [`host_features`](host-features.md) | Parse / validate `<ns>:<feature>` opt-in subselector tokens for emission gating |
| [`baseline`](baseline.md) | Deterministic SHA-256 emission baselines (capture / diff) used by regression tests |
| [`bridge_subagents`](bridge-subagents.md) | Per-agent Claude subagent stub emitter (bridge:copilot-vscode-to-claude:subagents) |
| [`bridge_subagents_goose`](bridge-subagents-goose.md) | Per-agent Goose stub-recipe emitter (bridge:`<src>`-to-goose:subagents) |
| [`bridge_sources`](bridge-sources.md) | Source-team inventory, file collection, hashing + bridge-freshness check (framework-aware) |
| [`hooks_emit`](hooks-emit.md) | Claude hooks settings + recursion-bounded guard emitter (bridge:copilot-vscode-to-claude:hooks) |
| [`instructions_split`](instructions-split.md) | Cache-aware CLAUDE.md layout: preamble + boundary + dynamic stanza (bridge:copilot-vscode-to-claude:cache-split) |
| [`schedule_emit`](schedule-emit.md) | `/schedule` routine spec emitter (bridge:copilot-vscode-to-claude:schedule) |
| [`goose_config`](goose-config.md) | Locate + safely mutate Goose's `config.yaml` for source/model switching (no key handling) |

## PR Management

| Module | Role |
|--------|------|
| [`pr_management`](pr-management.md) | Recipient registry, gh-CLI wrappers, stale-PR scan, end-of-task three-way disposition prompt |

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

The interoperability feature family has three dedicated modules:

1. [`convert`](convert.md) for format migration.
2. [`interop`](interop.md) for CAI normalization and transfer.
3. [`bridge`](bridge.md) for lightweight source-canonical runtime bridging.

For workflow-level usage and mode selection, see the [Interoperability](../interoperability.md) page.
