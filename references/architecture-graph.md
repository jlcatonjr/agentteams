<!-- AGENTTEAMS:BEGIN content v=1 -->
# agentteams — Repository Architecture Map

> **Auto-generated.** Regenerated on every commit that touches the `agentteams` package. Do not edit manually — changes will be overwritten.

- Modules mapped: **162**
- Packages: **7**
- Internal import edges: **339**
- Distinct external dependencies: **6**

---

## Package Dependency Diagram

Inter-package import dependencies (module-level detail in the tables below).

![agentteams package dependency diagram](architecture-graph.svg)

---

## Packages

| Package | Modules | Depends on |
| --- | --- | --- |
| `agentteams` | 95 | `agentteams.cli`, `agentteams.enrich`, `agentteams.frameworks`, `agentteams.research` |
| `agentteams.cli` | 24 | `agentteams`, `agentteams.frameworks`, `agentteams.redteam` |
| `agentteams.enrich` | 6 | `agentteams` |
| `agentteams.eval_adapters` | 2 | — |
| `agentteams.frameworks` | 10 | `agentteams` |
| `agentteams.redteam` | 16 | `agentteams`, `agentteams.frameworks`, `agentteams.research` |
| `agentteams.research` | 9 | — |

---

## Module Dependency Diagram

Every module, coloured by package (full adjacency in the table below).

![agentteams module dependencies](architecture-modules.svg)

---

## Module Dependency Table

| Module | Imports (internal) | Imported by |
| --- | --- | --- |
| `agentteams` | — | `agentteams.backup`, `agentteams.cli.artifacts`, `agentteams.cli.generate`, `agentteams.cli.parser`, `agentteams.git_hooks` |
| `agentteams._utils` | — | `agentteams.analyze`, `agentteams.ingest` |
| `agentteams.advisory` | — | — |
| `agentteams.ai_bad_habits` | — | `agentteams.cli.generate` |
| `agentteams.analyze` | `agentteams._utils`, `agentteams.host_features`, `agentteams.manifest_format`, `agentteams.mcp_detect`, `agentteams.mcp_emit`, `agentteams.output_plan`, `agentteams.recipe_fields`, `agentteams.tool_metadata_catalog` | `agentteams.cli.generate`, `agentteams.output_plan`, `agentteams.rank_conformance` |
| `agentteams.architecture` | `agentteams.backup`, `agentteams.svg_render` | `agentteams.git_hooks` |
| `agentteams.atomicio` | — | `agentteams.backup`, `agentteams.canonical`, `agentteams.cli.artifacts`, `agentteams.cli.grants`, `agentteams.cli.schema_cache`, `agentteams.cli.security_gate`, `agentteams.codex_mcp_emit`, `agentteams.emit`, `agentteams.enrich._enrich`, `agentteams.fence_inject`, `agentteams.fences`, `agentteams.hooks_emit`, `agentteams.liaison_logs`, `agentteams.mcp_emit`, `agentteams.plan_steps_todo`, `agentteams.redteam.findings_ledger`, `agentteams.schedule_emit`, `agentteams.sync_baseline`, `agentteams.sync_pin`, `agentteams.team_package` |
| `agentteams.audit` | `agentteams.audit_agent_contract`, `agentteams.audit_types`, `agentteams.backup`, `agentteams.living_doc` | `agentteams.cli.generate` |
| `agentteams.audit_agent_contract` | `agentteams.audit_types` | `agentteams.audit` |
| `agentteams.audit_types` | `agentteams.frameworks.registry` | `agentteams.audit`, `agentteams.audit_agent_contract`, `agentteams.cli.standalone_modes`, `agentteams.rank_conformance` |
| `agentteams.backup` | `agentteams`, `agentteams.atomicio`, `agentteams.liaison_logs` | `agentteams.architecture`, `agentteams.audit`, `agentteams.bridge`, `agentteams.cli.artifacts`, `agentteams.cli.code_index_artifacts`, `agentteams.cli.commands`, `agentteams.cli.output_target`, `agentteams.emit`, `agentteams.fence_inject`, `agentteams.fleet`, `agentteams.interop`, `agentteams.scan`, `agentteams.stale_detector`, `agentteams.stale_remediate` |
| `agentteams.baseline` | — | `agentteams.cli.app` |
| `agentteams.behavioral_drift` | `agentteams.handoff_payloads` | — |
| `agentteams.bridge` | `agentteams.backup`, `agentteams.bridge_pair_docs`, `agentteams.bridge_skills`, `agentteams.bridge_sources`, `agentteams.bridge_subagents`, `agentteams.bridge_subagents_goose`, `agentteams.canonical`, `agentteams.capability_hints`, `agentteams.frameworks.goose`, `agentteams.hooks_emit`, `agentteams.instructions_split`, `agentteams.interop`, `agentteams.parallel_plan`, `agentteams.plan_steps_todo`, `agentteams.schedule_emit` | `agentteams.cli.commands`, `agentteams.stale_detector`, `agentteams.team_package` |
| `agentteams.bridge_pair_docs` | `agentteams.canonical` | `agentteams.bridge` |
| `agentteams.bridge_skills` | — | `agentteams.bridge` |
| `agentteams.bridge_sources` | `agentteams.canonical`, `agentteams.yaml_frontmatter` | `agentteams.bridge`, `agentteams.redteam.instantiate` |
| `agentteams.bridge_subagents` | `agentteams.frameworks.claude` | `agentteams.bridge`, `agentteams.bridge_subagents_goose` |
| `agentteams.bridge_subagents_goose` | `agentteams.bridge_subagents`, `agentteams.frameworks.goose` | `agentteams.bridge` |
| `agentteams.budget` | — | `agentteams.cli.standalone_modes` |
| `agentteams.canonical` | `agentteams.atomicio`, `agentteams.interop`, `agentteams.yaml_frontmatter` | `agentteams.bridge`, `agentteams.bridge_pair_docs`, `agentteams.bridge_sources`, `agentteams.cli.commands`, `agentteams.interop`, `agentteams.interop_helpers`, `agentteams.multi_sync`, `agentteams.team_package` |
| `agentteams.capability_hints` | — | `agentteams.bridge`, `agentteams.frameworks.goose_docs` |
| `agentteams.capability_map` | `agentteams.yaml_frontmatter` | `agentteams.frameworks.goose`, `agentteams.interop`, `agentteams.rank_conformance` |
| `agentteams.cli` | — | — |
| `agentteams.cli.app` | `agentteams.baseline`, `agentteams.cli.commands`, `agentteams.cli.generate`, `agentteams.cli.goose_switch`, `agentteams.cli.json_mode`, `agentteams.cli.package_switch`, `agentteams.cli.parser`, `agentteams.cli.recipe_check`, `agentteams.cli.render_pipeline`, `agentteams.cli.sync_switch`, `agentteams.fence_inject`, `agentteams.fleet`, `agentteams.frameworks.goose`, `agentteams.git_hooks`, `agentteams.host_features` | — |
| `agentteams.cli.artifacts` | `agentteams`, `agentteams.atomicio`, `agentteams.backup`, `agentteams.cli.code_index_artifacts`, `agentteams.cli.grants`, `agentteams.cli.schema_cache`, `agentteams.codex_mcp_emit`, `agentteams.drift`, `agentteams.errors`, `agentteams.eval_suite`, `agentteams.fences`, `agentteams.frameworks.claude`, `agentteams.host_features`, `agentteams.mcp_emit`, `agentteams.memory_index`, `agentteams.memory_index_incremental`, `agentteams.model_routing` | `agentteams.cli.generate`, `agentteams.cli.standalone_modes`, `agentteams.git_hooks` |
| `agentteams.cli.backup_switch` | `agentteams.emit` | `agentteams.cli.parser` |
| `agentteams.cli.code_index_artifacts` | `agentteams.backup`, `agentteams.cli.schema_cache`, `agentteams.code_index`, `agentteams.code_sources`, `agentteams.errors` | `agentteams.cli.artifacts` |
| `agentteams.cli.commands` | `agentteams.backup`, `agentteams.bridge`, `agentteams.canonical`, `agentteams.cli.grants`, `agentteams.cli.security_gate`, `agentteams.convert`, `agentteams.drift`, `agentteams.emit`, `agentteams.frameworks.registry`, `agentteams.integrity`, `agentteams.interop`, `agentteams.redteam.cycle`, `agentteams.redteam.freshness`, `agentteams.research`, `agentteams.security_refs`, `agentteams.stale_detector`, `agentteams.stale_remediate`, `agentteams.sync_baseline`, `agentteams.sync_classifier` | `agentteams.cli.app`, `agentteams.stale_remediate` |
| `agentteams.cli.decision_log` | — | `agentteams.cli.grants`, `agentteams.cli.security_gate` |
| `agentteams.cli.exit_codes` | `agentteams.emit` | `agentteams.cli.generate` |
| `agentteams.cli.fleet_switch` | — | `agentteams.cli.parser` |
| `agentteams.cli.generate` | `agentteams`, `agentteams.ai_bad_habits`, `agentteams.analyze`, `agentteams.audit`, `agentteams.cli.artifacts`, `agentteams.cli.exit_codes`, `agentteams.cli.json_mode`, `agentteams.cli.output_target`, `agentteams.cli.post_emit_checks`, `agentteams.cli.render_pipeline`, `agentteams.cli.security_gate`, `agentteams.cli.standalone_modes`, `agentteams.drift`, `agentteams.emit`, `agentteams.enrich`, `agentteams.errors`, `agentteams.framework_research`, `agentteams.frameworks.registry`, `agentteams.front_matter_reconcile`, `agentteams.git_hooks`, `agentteams.graph`, `agentteams.ingest`, `agentteams.integrity`, `agentteams.liaison_logs`, `agentteams.render`, `agentteams.security_refs`, `agentteams.template_pins`, `agentteams.update_report` | `agentteams.cli.app` |
| `agentteams.cli.goose_switch` | `agentteams.goose_config` | `agentteams.cli.app`, `agentteams.cli.parser` |
| `agentteams.cli.grants` | `agentteams.atomicio`, `agentteams.cli.decision_log`, `agentteams.cli.signed_ledger` | `agentteams.cli.artifacts`, `agentteams.cli.commands` |
| `agentteams.cli.json_mode` | — | `agentteams.cli.app`, `agentteams.cli.generate` |
| `agentteams.cli.output_target` | `agentteams.backup` | `agentteams.cli.generate` |
| `agentteams.cli.package_switch` | `agentteams.cli.security_gate`, `agentteams.security_refs`, `agentteams.team_package` | `agentteams.cli.app`, `agentteams.cli.parser` |
| `agentteams.cli.parser` | `agentteams`, `agentteams.cli.backup_switch`, `agentteams.cli.fleet_switch`, `agentteams.cli.goose_switch`, `agentteams.cli.package_switch`, `agentteams.cli.parser_validate`, `agentteams.cli.sync_switch`, `agentteams.emit`, `agentteams.frameworks.registry` | `agentteams.cli.app` |
| `agentteams.cli.parser_validate` | — | `agentteams.cli.parser` |
| `agentteams.cli.post_emit_checks` | `agentteams.emit`, `agentteams.scan` | `agentteams.cli.generate` |
| `agentteams.cli.recipe_check` | `agentteams.frameworks.goose` | `agentteams.cli.app` |
| `agentteams.cli.render_pipeline` | `agentteams.emit`, `agentteams.frameworks.agents_md`, `agentteams.frameworks.base`, `agentteams.frameworks.claude`, `agentteams.frameworks.copilot_cli`, `agentteams.frameworks.copilot_vscode`, `agentteams.frameworks.goose`, `agentteams.graph`, `agentteams.render`, `agentteams.vscode_tasks` | `agentteams.cli.app`, `agentteams.cli.generate` |
| `agentteams.cli.schema_cache` | `agentteams.atomicio` | `agentteams.cli.artifacts`, `agentteams.cli.code_index_artifacts`, `agentteams.security_refs` |
| `agentteams.cli.security_gate` | `agentteams.atomicio`, `agentteams.cli.decision_log` | `agentteams.cli.commands`, `agentteams.cli.generate`, `agentteams.cli.package_switch`, `agentteams.cli.standalone_modes`, `agentteams.security_refs` |
| `agentteams.cli.signed_ledger` | — | `agentteams.cli.grants` |
| `agentteams.cli.standalone_modes` | `agentteams.audit_types`, `agentteams.budget`, `agentteams.cli.artifacts`, `agentteams.cli.security_gate`, `agentteams.emit`, `agentteams.rank_conformance`, `agentteams.scan`, `agentteams.template_pins` | `agentteams.cli.generate` |
| `agentteams.cli.sync_switch` | `agentteams.multi_sync` | `agentteams.cli.app`, `agentteams.cli.parser` |
| `agentteams.code_index` | — | `agentteams.cli.code_index_artifacts`, `agentteams.code_sources` |
| `agentteams.code_sources` | `agentteams.code_index` | `agentteams.cli.code_index_artifacts` |
| `agentteams.codex_mcp_emit` | `agentteams.atomicio`, `agentteams.mcp_emit`, `agentteams.toml_write` | `agentteams.cli.artifacts` |
| `agentteams.convert` | `agentteams.frameworks.base`, `agentteams.frameworks.registry` | `agentteams.cli.commands` |
| `agentteams.drift` | `agentteams.emit` | `agentteams.cli.artifacts`, `agentteams.cli.commands`, `agentteams.cli.generate`, `agentteams.emit`, `agentteams.stale_detector` |
| `agentteams.emit` | `agentteams.atomicio`, `agentteams.backup`, `agentteams.drift`, `agentteams.fence_inject`, `agentteams.fences` | `agentteams.cli.backup_switch`, `agentteams.cli.commands`, `agentteams.cli.exit_codes`, `agentteams.cli.generate`, `agentteams.cli.parser`, `agentteams.cli.post_emit_checks`, `agentteams.cli.render_pipeline`, `agentteams.cli.standalone_modes`, `agentteams.drift`, `agentteams.fence_inject`, `agentteams.git_hooks` |
| `agentteams.enrich` | `agentteams.enrich._audit`, `agentteams.enrich._enrich`, `agentteams.enrich._models`, `agentteams.enrich._tools` | `agentteams.cli.generate` |
| `agentteams.enrich._audit` | `agentteams.enrich._fills`, `agentteams.enrich._models`, `agentteams.enrich._tools`, `agentteams.tool_metadata_catalog` | `agentteams.enrich` |
| `agentteams.enrich._enrich` | `agentteams.atomicio`, `agentteams.enrich._fills`, `agentteams.enrich._models`, `agentteams.enrich._notebooks`, `agentteams.enrich._tools` | `agentteams.enrich` |
| `agentteams.enrich._fills` | — | `agentteams.enrich._audit`, `agentteams.enrich._enrich` |
| `agentteams.enrich._models` | — | `agentteams.enrich`, `agentteams.enrich._audit`, `agentteams.enrich._enrich`, `agentteams.enrich._notebooks` |
| `agentteams.enrich._notebooks` | `agentteams.enrich._models`, `agentteams.enrich._tools`, `agentteams.tool_metadata_catalog` | `agentteams.enrich._enrich` |
| `agentteams.enrich._tools` | `agentteams.tool_metadata_catalog` | `agentteams.enrich`, `agentteams.enrich._audit`, `agentteams.enrich._enrich`, `agentteams.enrich._notebooks` |
| `agentteams.errors` | — | `agentteams.cli.artifacts`, `agentteams.cli.code_index_artifacts`, `agentteams.cli.generate`, `agentteams.git_hooks`, `agentteams.template_pins` |
| `agentteams.eval_adapters` | — | — |
| `agentteams.eval_adapters.inspect_ai` | — | — |
| `agentteams.eval_adapters.openai_evals` | — | — |
| `agentteams.eval_suite` | — | `agentteams.cli.artifacts` |
| `agentteams.feature_audit` | — | — |
| `agentteams.fence_inject` | `agentteams.atomicio`, `agentteams.backup`, `agentteams.emit` | `agentteams.cli.app`, `agentteams.emit` |
| `agentteams.fences` | `agentteams.atomicio`, `agentteams.front_matter_merge`, `agentteams.unfenced` | `agentteams.cli.artifacts`, `agentteams.emit`, `agentteams.interop` |
| `agentteams.fleet` | `agentteams.backup` | `agentteams.cli.app`, `agentteams.redteam.realcopy`, `agentteams.stale_detector`, `agentteams.stale_remediate` |
| `agentteams.framework_research` | — | `agentteams.cli.generate` |
| `agentteams.frameworks` | — | — |
| `agentteams.frameworks.agents_md` | `agentteams.frameworks.base`, `agentteams.yaml_frontmatter` | `agentteams.cli.render_pipeline`, `agentteams.frameworks.codex`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.base` | `agentteams.yaml_frontmatter` | `agentteams.cli.render_pipeline`, `agentteams.convert`, `agentteams.frameworks.agents_md`, `agentteams.frameworks.claude`, `agentteams.frameworks.copilot_cli`, `agentteams.frameworks.copilot_vscode`, `agentteams.frameworks.goose`, `agentteams.frameworks.registry`, `agentteams.interop` |
| `agentteams.frameworks.claude` | `agentteams.frameworks.base`, `agentteams.yaml_frontmatter` | `agentteams.bridge_subagents`, `agentteams.cli.artifacts`, `agentteams.cli.render_pipeline`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.codex` | `agentteams.frameworks.agents_md` | `agentteams.frameworks.registry` |
| `agentteams.frameworks.copilot_cli` | `agentteams.frameworks.base`, `agentteams.frameworks.copilot_vscode`, `agentteams.yaml_frontmatter` | `agentteams.cli.render_pipeline`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.copilot_vscode` | `agentteams.frameworks.base`, `agentteams.yaml_frontmatter` | `agentteams.cli.render_pipeline`, `agentteams.frameworks.copilot_cli`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.goose` | `agentteams.capability_map`, `agentteams.frameworks.base`, `agentteams.frameworks.goose_docs`, `agentteams.frameworks.goose_recipe_read`, `agentteams.yaml_frontmatter` | `agentteams.bridge`, `agentteams.bridge_subagents_goose`, `agentteams.cli.app`, `agentteams.cli.recipe_check`, `agentteams.cli.render_pipeline`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.goose_docs` | `agentteams.capability_hints` | `agentteams.frameworks.goose` |
| `agentteams.frameworks.goose_recipe_read` | — | `agentteams.frameworks.goose` |
| `agentteams.frameworks.registry` | `agentteams.frameworks.agents_md`, `agentteams.frameworks.base`, `agentteams.frameworks.claude`, `agentteams.frameworks.codex`, `agentteams.frameworks.copilot_cli`, `agentteams.frameworks.copilot_vscode`, `agentteams.frameworks.goose` | `agentteams.audit_types`, `agentteams.cli.commands`, `agentteams.cli.generate`, `agentteams.cli.parser`, `agentteams.convert`, `agentteams.interop`, `agentteams.manifest_format`, `agentteams.multi_sync`, `agentteams.redteam.instantiate`, `agentteams.redteam.sweep`, `agentteams.render` |
| `agentteams.front_matter_merge` | — | `agentteams.fences`, `agentteams.front_matter_reconcile`, `agentteams.sync_classifier`, `agentteams.unfenced` |
| `agentteams.front_matter_reconcile` | `agentteams.front_matter_merge`, `agentteams.yaml_frontmatter` | `agentteams.cli.generate` |
| `agentteams.git_hooks` | `agentteams`, `agentteams.architecture`, `agentteams.cli.artifacts`, `agentteams.emit`, `agentteams.errors`, `agentteams.graph` | `agentteams.cli.app`, `agentteams.cli.generate` |
| `agentteams.goose_config` | — | `agentteams.cli.goose_switch` |
| `agentteams.graph` | `agentteams.graph_inputs`, `agentteams.svg_render` | `agentteams.cli.generate`, `agentteams.cli.render_pipeline`, `agentteams.git_hooks` |
| `agentteams.graph_inputs` | `agentteams.yaml_frontmatter` | `agentteams.graph` |
| `agentteams.handoff_payloads` | — | `agentteams.behavioral_drift` |
| `agentteams.hooks_emit` | `agentteams.atomicio` | `agentteams.bridge` |
| `agentteams.host_features` | — | `agentteams.analyze`, `agentteams.cli.app`, `agentteams.cli.artifacts` |
| `agentteams.ingest` | `agentteams._utils` | `agentteams.cli.generate` |
| `agentteams.instructions_split` | — | `agentteams.bridge` |
| `agentteams.integrity` | — | `agentteams.cli.commands`, `agentteams.cli.generate`, `agentteams.redteam.checks_static`, `agentteams.redteam.runner` |
| `agentteams.interop` | `agentteams.backup`, `agentteams.canonical`, `agentteams.capability_map`, `agentteams.fences`, `agentteams.frameworks.base`, `agentteams.frameworks.registry`, `agentteams.interop_helpers`, `agentteams.mcp_emit`, `agentteams.yaml_frontmatter` | `agentteams.bridge`, `agentteams.canonical`, `agentteams.cli.commands`, `agentteams.multi_sync`, `agentteams.team_package` |
| `agentteams.interop_helpers` | `agentteams.canonical`, `agentteams.yaml_frontmatter` | `agentteams.interop` |
| `agentteams.liaison_logs` | `agentteams.atomicio` | `agentteams.backup`, `agentteams.cli.generate` |
| `agentteams.living_doc` | — | `agentteams.audit` |
| `agentteams.man` | — | — |
| `agentteams.manifest_format` | `agentteams.frameworks.registry` | `agentteams.analyze` |
| `agentteams.mcp_detect` | — | `agentteams.analyze` |
| `agentteams.mcp_emit` | `agentteams.atomicio` | `agentteams.analyze`, `agentteams.cli.artifacts`, `agentteams.codex_mcp_emit`, `agentteams.interop` |
| `agentteams.memory_index` | — | `agentteams.cli.artifacts`, `agentteams.memory_index_incremental` |
| `agentteams.memory_index_incremental` | `agentteams.memory_index` | `agentteams.cli.artifacts` |
| `agentteams.model_routing` | — | `agentteams.cli.artifacts` |
| `agentteams.multi_sync` | `agentteams.canonical`, `agentteams.frameworks.registry`, `agentteams.interop`, `agentteams.sync_baseline`, `agentteams.sync_classifier`, `agentteams.sync_pin` | `agentteams.cli.sync_switch` |
| `agentteams.output_plan` | `agentteams.analyze` | `agentteams.analyze` |
| `agentteams.parallel_plan` | — | `agentteams.bridge` |
| `agentteams.plan_steps` | — | `agentteams.session_scan` |
| `agentteams.plan_steps_todo` | `agentteams.atomicio` | `agentteams.bridge` |
| `agentteams.pr_management` | — | — |
| `agentteams.provenance` | — | — |
| `agentteams.rank_conformance` | `agentteams.analyze`, `agentteams.audit_types`, `agentteams.capability_map` | `agentteams.cli.standalone_modes` |
| `agentteams.recipe_fields` | — | `agentteams.analyze` |
| `agentteams.redteam` | — | — |
| `agentteams.redteam.budget` | — | — |
| `agentteams.redteam.checks_report` | `agentteams.redteam.registry` | `agentteams.redteam.cycle`, `agentteams.redteam.selfaudit` |
| `agentteams.redteam.checks_static` | `agentteams.integrity`, `agentteams.redteam.registry` | `agentteams.redteam.selfaudit` |
| `agentteams.redteam.corpus` | `agentteams.scan` | `agentteams.redteam.runner` |
| `agentteams.redteam.coverage` | `agentteams.redteam.registry` | — |
| `agentteams.redteam.cycle` | `agentteams.redteam.checks_report`, `agentteams.redteam.kev_correlation`, `agentteams.redteam.realcopy`, `agentteams.redteam.registry`, `agentteams.redteam.report`, `agentteams.redteam.runner`, `agentteams.redteam.selfaudit` | `agentteams.cli.commands` |
| `agentteams.redteam.findings_ledger` | `agentteams.atomicio` | — |
| `agentteams.redteam.freshness` | `agentteams.research.search` | `agentteams.cli.commands` |
| `agentteams.redteam.instantiate` | `agentteams.bridge_sources`, `agentteams.frameworks.registry` | — |
| `agentteams.redteam.kev_correlation` | — | `agentteams.redteam.cycle` |
| `agentteams.redteam.realcopy` | `agentteams.fleet` | `agentteams.redteam.cycle` |
| `agentteams.redteam.registry` | — | `agentteams.redteam.checks_report`, `agentteams.redteam.checks_static`, `agentteams.redteam.coverage`, `agentteams.redteam.cycle`, `agentteams.redteam.report`, `agentteams.redteam.runner`, `agentteams.redteam.selfaudit` |
| `agentteams.redteam.report` | `agentteams.redteam.registry`, `agentteams.redteam.runner`, `agentteams.redteam.selfaudit` | `agentteams.redteam.cycle` |
| `agentteams.redteam.runner` | `agentteams.integrity`, `agentteams.redteam.corpus`, `agentteams.redteam.registry` | `agentteams.redteam.cycle`, `agentteams.redteam.report` |
| `agentteams.redteam.selfaudit` | `agentteams.redteam.checks_report`, `agentteams.redteam.checks_static`, `agentteams.redteam.registry` | `agentteams.redteam.cycle`, `agentteams.redteam.report` |
| `agentteams.redteam.sweep` | `agentteams.frameworks.registry` | — |
| `agentteams.remediate` | — | — |
| `agentteams.render` | `agentteams.frameworks.registry` | `agentteams.cli.generate`, `agentteams.cli.render_pipeline`, `agentteams.template_pins` |
| `agentteams.research` | `agentteams.research.backends`, `agentteams.research.news`, `agentteams.research.reputable`, `agentteams.research.scholarly`, `agentteams.research.search`, `agentteams.research.verify` | `agentteams.cli.commands` |
| `agentteams.research.__main__` | `agentteams.research.browser`, `agentteams.research.scholarly`, `agentteams.research.search` | — |
| `agentteams.research.backends` | — | `agentteams.research`, `agentteams.research.search` |
| `agentteams.research.browser` | `agentteams.research.search` | `agentteams.research.__main__` |
| `agentteams.research.cache` | — | `agentteams.research.scholarly`, `agentteams.research.search` |
| `agentteams.research.news` | `agentteams.research.reputable` | `agentteams.research` |
| `agentteams.research.reputable` | `agentteams.research.search` | `agentteams.research`, `agentteams.research.news` |
| `agentteams.research.scholarly` | `agentteams.research.cache` | `agentteams.research`, `agentteams.research.__main__` |
| `agentteams.research.search` | `agentteams.research.backends`, `agentteams.research.cache` | `agentteams.redteam.freshness`, `agentteams.research`, `agentteams.research.__main__`, `agentteams.research.browser`, `agentteams.research.reputable` |
| `agentteams.research.verify` | — | `agentteams.research` |
| `agentteams.scan` | `agentteams.backup` | `agentteams.cli.post_emit_checks`, `agentteams.cli.standalone_modes`, `agentteams.redteam.corpus` |
| `agentteams.schedule_emit` | `agentteams.atomicio` | `agentteams.bridge` |
| `agentteams.security_feed_render` | — | `agentteams.security_refs` |
| `agentteams.security_refs` | `agentteams.cli.schema_cache`, `agentteams.cli.security_gate`, `agentteams.security_feed_render` | `agentteams.cli.commands`, `agentteams.cli.generate`, `agentteams.cli.package_switch` |
| `agentteams.session_scan` | `agentteams.plan_steps` | — |
| `agentteams.stale_detector` | `agentteams.backup`, `agentteams.bridge`, `agentteams.drift`, `agentteams.fleet` | `agentteams.cli.commands`, `agentteams.stale_remediate` |
| `agentteams.stale_remediate` | `agentteams.backup`, `agentteams.cli.commands`, `agentteams.fleet`, `agentteams.stale_detector` | `agentteams.cli.commands` |
| `agentteams.svg_render` | — | `agentteams.architecture`, `agentteams.graph` |
| `agentteams.sync_baseline` | `agentteams.atomicio` | `agentteams.cli.commands`, `agentteams.multi_sync` |
| `agentteams.sync_classifier` | `agentteams.front_matter_merge` | `agentteams.cli.commands`, `agentteams.multi_sync` |
| `agentteams.sync_pin` | `agentteams.atomicio` | `agentteams.multi_sync` |
| `agentteams.team_package` | `agentteams.atomicio`, `agentteams.bridge`, `agentteams.canonical`, `agentteams.interop` | `agentteams.cli.package_switch` |
| `agentteams.template_pins` | `agentteams.errors`, `agentteams.render` | `agentteams.cli.generate`, `agentteams.cli.standalone_modes` |
| `agentteams.toml_write` | — | `agentteams.codex_mcp_emit` |
| `agentteams.tool_metadata_catalog` | — | `agentteams.analyze`, `agentteams.enrich._audit`, `agentteams.enrich._notebooks`, `agentteams.enrich._tools` |
| `agentteams.unfenced` | `agentteams.front_matter_merge` | `agentteams.fences` |
| `agentteams.update_report` | — | `agentteams.cli.generate` |
| `agentteams.vscode_tasks` | — | `agentteams.cli.render_pipeline` |
| `agentteams.yaml_frontmatter` | — | `agentteams.bridge_sources`, `agentteams.canonical`, `agentteams.capability_map`, `agentteams.frameworks.agents_md`, `agentteams.frameworks.base`, `agentteams.frameworks.claude`, `agentteams.frameworks.copilot_cli`, `agentteams.frameworks.copilot_vscode`, `agentteams.frameworks.goose`, `agentteams.front_matter_reconcile`, `agentteams.graph_inputs`, `agentteams.interop`, `agentteams.interop_helpers` |

---

## External Dependencies

Third-party (non-stdlib) top-level packages imported by the mapped package:

`httpx`, `jsonschema`, `playwright`, `pypdf`, `referencing`, `yaml`

**Repo-local (outside the mapped package):** `build_team`

---

## Diagram Source

<details>
<summary>Mermaid &amp; DOT source for the diagram above</summary>

```mermaid
flowchart LR
    classDef root fill:#e8eefb,stroke:#1b3fa0,color:#000
    classDef sub  fill:#eef6ee,stroke:#3f8f4f,color:#000
    agentteams["agentteams"]
    class agentteams root
    agentteams_cli["agentteams.cli"]
    class agentteams_cli sub
    agentteams_enrich["agentteams.enrich"]
    class agentteams_enrich sub
    agentteams_eval_adapters["agentteams.eval_adapters"]
    class agentteams_eval_adapters sub
    agentteams_frameworks["agentteams.frameworks"]
    class agentteams_frameworks sub
    agentteams_redteam["agentteams.redteam"]
    class agentteams_redteam sub
    agentteams_research["agentteams.research"]
    class agentteams_research sub
    agentteams --> agentteams_cli
    agentteams --> agentteams_enrich
    agentteams --> agentteams_frameworks
    agentteams --> agentteams_research
    agentteams_cli --> agentteams
    agentteams_cli --> agentteams_frameworks
    agentteams_cli --> agentteams_redteam
    agentteams_enrich --> agentteams
    agentteams_frameworks --> agentteams
    agentteams_redteam --> agentteams
    agentteams_redteam --> agentteams_frameworks
    agentteams_redteam --> agentteams_research
```

```dot
digraph "agentteams architecture" {
    rankdir=LR;
    node [fontname="Helvetica", fontsize=11, shape=box, style="rounded,filled", fillcolor="#eef6ee"];
    edge [fontsize=9];
    "agentteams" [fillcolor="#e8eefb"];
    "agentteams.cli" [fillcolor="#eef6ee"];
    "agentteams.enrich" [fillcolor="#eef6ee"];
    "agentteams.eval_adapters" [fillcolor="#eef6ee"];
    "agentteams.frameworks" [fillcolor="#eef6ee"];
    "agentteams.redteam" [fillcolor="#eef6ee"];
    "agentteams.research" [fillcolor="#eef6ee"];
    "agentteams" -> "agentteams.cli";
    "agentteams" -> "agentteams.enrich";
    "agentteams" -> "agentteams.frameworks";
    "agentteams" -> "agentteams.research";
    "agentteams.cli" -> "agentteams";
    "agentteams.cli" -> "agentteams.frameworks";
    "agentteams.cli" -> "agentteams.redteam";
    "agentteams.enrich" -> "agentteams";
    "agentteams.frameworks" -> "agentteams";
    "agentteams.redteam" -> "agentteams";
    "agentteams.redteam" -> "agentteams.frameworks";
    "agentteams.redteam" -> "agentteams.research";
}
```

</details>

---

## JSON (module-level)

```json
{
  "root_package": "agentteams",
  "modules": {
    "agentteams": {
      "package": "agentteams",
      "path": "agentteams/__init__.py",
      "is_package": true,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams._utils": {
      "package": "agentteams",
      "path": "agentteams/_utils.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.advisory": {
      "package": "agentteams",
      "path": "agentteams/advisory.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.ai_bad_habits": {
      "package": "agentteams",
      "path": "agentteams/ai_bad_habits.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.analyze": {
      "package": "agentteams",
      "path": "agentteams/analyze.py",
      "is_package": false,
      "imports_internal": [
        "agentteams._utils",
        "agentteams.host_features",
        "agentteams.manifest_format",
        "agentteams.mcp_detect",
        "agentteams.mcp_emit",
        "agentteams.output_plan",
        "agentteams.recipe_fields",
        "agentteams.tool_metadata_catalog"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.architecture": {
      "package": "agentteams",
      "path": "agentteams/architecture.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup",
        "agentteams.svg_render"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.atomicio": {
      "package": "agentteams",
      "path": "agentteams/atomicio.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.audit": {
      "package": "agentteams",
      "path": "agentteams/audit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.audit_agent_contract",
        "agentteams.audit_types",
        "agentteams.backup",
        "agentteams.living_doc"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.audit_agent_contract": {
      "package": "agentteams",
      "path": "agentteams/audit_agent_contract.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.audit_types"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.audit_types": {
      "package": "agentteams",
      "path": "agentteams/audit_types.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.backup": {
      "package": "agentteams",
      "path": "agentteams/backup.py",
      "is_package": false,
      "imports_internal": [
        "agentteams",
        "agentteams.atomicio",
        "agentteams.liaison_logs"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.baseline": {
      "package": "agentteams",
      "path": "agentteams/baseline.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.behavioral_drift": {
      "package": "agentteams",
      "path": "agentteams/behavioral_drift.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.handoff_payloads"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.bridge": {
      "package": "agentteams",
      "path": "agentteams/bridge.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup",
        "agentteams.bridge_pair_docs",
        "agentteams.bridge_skills",
        "agentteams.bridge_sources",
        "agentteams.bridge_subagents",
        "agentteams.bridge_subagents_goose",
        "agentteams.canonical",
        "agentteams.capability_hints",
        "agentteams.frameworks.goose",
        "agentteams.hooks_emit",
        "agentteams.instructions_split",
        "agentteams.interop",
        "agentteams.parallel_plan",
        "agentteams.plan_steps_todo",
        "agentteams.schedule_emit"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.bridge_pair_docs": {
      "package": "agentteams",
      "path": "agentteams/bridge_pair_docs.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.canonical"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.bridge_skills": {
      "package": "agentteams",
      "path": "agentteams/bridge_skills.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.bridge_sources": {
      "package": "agentteams",
      "path": "agentteams/bridge_sources.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.canonical",
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.bridge_subagents": {
      "package": "agentteams",
      "path": "agentteams/bridge_subagents.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.claude"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.bridge_subagents_goose": {
      "package": "agentteams",
      "path": "agentteams/bridge_subagents_goose.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.bridge_subagents",
        "agentteams.frameworks.goose"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.budget": {
      "package": "agentteams",
      "path": "agentteams/budget.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.canonical": {
      "package": "agentteams",
      "path": "agentteams/canonical.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.interop",
        "agentteams.yaml_frontmatter"
      ],
      "external": [
        "jsonschema",
        "referencing",
        "yaml"
      ],
      "repo_local": []
    },
    "agentteams.capability_hints": {
      "package": "agentteams",
      "path": "agentteams/capability_hints.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.capability_map": {
      "package": "agentteams",
      "path": "agentteams/capability_map.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli": {
      "package": "agentteams",
      "path": "agentteams/cli/__init__.py",
      "is_package": true,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.app": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/app.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.baseline",
        "agentteams.cli.commands",
        "agentteams.cli.generate",
        "agentteams.cli.goose_switch",
        "agentteams.cli.json_mode",
        "agentteams.cli.package_switch",
        "agentteams.cli.parser",
        "agentteams.cli.recipe_check",
        "agentteams.cli.render_pipeline",
        "agentteams.cli.sync_switch",
        "agentteams.fence_inject",
        "agentteams.fleet",
        "agentteams.frameworks.goose",
        "agentteams.git_hooks",
        "agentteams.host_features"
      ],
      "external": [],
      "repo_local": [
        "build_team"
      ]
    },
    "agentteams.cli.artifacts": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/artifacts.py",
      "is_package": false,
      "imports_internal": [
        "agentteams",
        "agentteams.atomicio",
        "agentteams.backup",
        "agentteams.cli.code_index_artifacts",
        "agentteams.cli.grants",
        "agentteams.cli.schema_cache",
        "agentteams.codex_mcp_emit",
        "agentteams.drift",
        "agentteams.errors",
        "agentteams.eval_suite",
        "agentteams.fences",
        "agentteams.frameworks.claude",
        "agentteams.host_features",
        "agentteams.mcp_emit",
        "agentteams.memory_index",
        "agentteams.memory_index_incremental",
        "agentteams.model_routing"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.backup_switch": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/backup_switch.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.emit"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.code_index_artifacts": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/code_index_artifacts.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup",
        "agentteams.cli.schema_cache",
        "agentteams.code_index",
        "agentteams.code_sources",
        "agentteams.errors"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.commands": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/commands.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup",
        "agentteams.bridge",
        "agentteams.canonical",
        "agentteams.cli.grants",
        "agentteams.cli.security_gate",
        "agentteams.convert",
        "agentteams.drift",
        "agentteams.emit",
        "agentteams.frameworks.registry",
        "agentteams.integrity",
        "agentteams.interop",
        "agentteams.redteam.cycle",
        "agentteams.redteam.freshness",
        "agentteams.research",
        "agentteams.security_refs",
        "agentteams.stale_detector",
        "agentteams.stale_remediate",
        "agentteams.sync_baseline",
        "agentteams.sync_classifier"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.decision_log": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/decision_log.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.exit_codes": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/exit_codes.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.emit"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.fleet_switch": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/fleet_switch.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.generate": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/generate.py",
      "is_package": false,
      "imports_internal": [
        "agentteams",
        "agentteams.ai_bad_habits",
        "agentteams.analyze",
        "agentteams.audit",
        "agentteams.cli.artifacts",
        "agentteams.cli.exit_codes",
        "agentteams.cli.json_mode",
        "agentteams.cli.output_target",
        "agentteams.cli.post_emit_checks",
        "agentteams.cli.render_pipeline",
        "agentteams.cli.security_gate",
        "agentteams.cli.standalone_modes",
        "agentteams.drift",
        "agentteams.emit",
        "agentteams.enrich",
        "agentteams.errors",
        "agentteams.framework_research",
        "agentteams.frameworks.registry",
        "agentteams.front_matter_reconcile",
        "agentteams.git_hooks",
        "agentteams.graph",
        "agentteams.ingest",
        "agentteams.integrity",
        "agentteams.liaison_logs",
        "agentteams.render",
        "agentteams.security_refs",
        "agentteams.template_pins",
        "agentteams.update_report"
      ],
      "external": [],
      "repo_local": [
        "build_team"
      ]
    },
    "agentteams.cli.goose_switch": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/goose_switch.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.goose_config"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.grants": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/grants.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.cli.decision_log",
        "agentteams.cli.signed_ledger"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.json_mode": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/json_mode.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.output_target": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/output_target.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.package_switch": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/package_switch.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.cli.security_gate",
        "agentteams.security_refs",
        "agentteams.team_package"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.parser": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/parser.py",
      "is_package": false,
      "imports_internal": [
        "agentteams",
        "agentteams.cli.backup_switch",
        "agentteams.cli.fleet_switch",
        "agentteams.cli.goose_switch",
        "agentteams.cli.package_switch",
        "agentteams.cli.parser_validate",
        "agentteams.cli.sync_switch",
        "agentteams.emit",
        "agentteams.frameworks.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.parser_validate": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/parser_validate.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.post_emit_checks": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/post_emit_checks.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.emit",
        "agentteams.scan"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.recipe_check": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/recipe_check.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.goose"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.render_pipeline": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/render_pipeline.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.emit",
        "agentteams.frameworks.agents_md",
        "agentteams.frameworks.base",
        "agentteams.frameworks.claude",
        "agentteams.frameworks.copilot_cli",
        "agentteams.frameworks.copilot_vscode",
        "agentteams.frameworks.goose",
        "agentteams.graph",
        "agentteams.render",
        "agentteams.vscode_tasks"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.schema_cache": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/schema_cache.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": [
        "jsonschema"
      ],
      "repo_local": []
    },
    "agentteams.cli.security_gate": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/security_gate.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.cli.decision_log"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.signed_ledger": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/signed_ledger.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.standalone_modes": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/standalone_modes.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.audit_types",
        "agentteams.budget",
        "agentteams.cli.artifacts",
        "agentteams.cli.security_gate",
        "agentteams.emit",
        "agentteams.rank_conformance",
        "agentteams.scan",
        "agentteams.template_pins"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.cli.sync_switch": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/sync_switch.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.multi_sync"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.code_index": {
      "package": "agentteams",
      "path": "agentteams/code_index.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.code_sources": {
      "package": "agentteams",
      "path": "agentteams/code_sources.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.code_index"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.codex_mcp_emit": {
      "package": "agentteams",
      "path": "agentteams/codex_mcp_emit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.mcp_emit",
        "agentteams.toml_write"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.convert": {
      "package": "agentteams",
      "path": "agentteams/convert.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base",
        "agentteams.frameworks.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.drift": {
      "package": "agentteams",
      "path": "agentteams/drift.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.emit"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.emit": {
      "package": "agentteams",
      "path": "agentteams/emit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.backup",
        "agentteams.drift",
        "agentteams.fence_inject",
        "agentteams.fences"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.enrich": {
      "package": "agentteams",
      "path": "agentteams/enrich/__init__.py",
      "is_package": true,
      "imports_internal": [
        "agentteams.enrich._audit",
        "agentteams.enrich._enrich",
        "agentteams.enrich._models",
        "agentteams.enrich._tools"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.enrich._audit": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_audit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.enrich._fills",
        "agentteams.enrich._models",
        "agentteams.enrich._tools",
        "agentteams.tool_metadata_catalog"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.enrich._enrich": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_enrich.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.enrich._fills",
        "agentteams.enrich._models",
        "agentteams.enrich._notebooks",
        "agentteams.enrich._tools"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.enrich._fills": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_fills.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.enrich._models": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_models.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.enrich._notebooks": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_notebooks.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.enrich._models",
        "agentteams.enrich._tools",
        "agentteams.tool_metadata_catalog"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.enrich._tools": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_tools.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.tool_metadata_catalog"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.errors": {
      "package": "agentteams",
      "path": "agentteams/errors.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.eval_adapters": {
      "package": "agentteams",
      "path": "agentteams/eval_adapters/__init__.py",
      "is_package": true,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.eval_adapters.inspect_ai": {
      "package": "agentteams.eval_adapters",
      "path": "agentteams/eval_adapters/inspect_ai.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.eval_adapters.openai_evals": {
      "package": "agentteams.eval_adapters",
      "path": "agentteams/eval_adapters/openai_evals.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.eval_suite": {
      "package": "agentteams",
      "path": "agentteams/eval_suite.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.feature_audit": {
      "package": "agentteams",
      "path": "agentteams/feature_audit.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.fence_inject": {
      "package": "agentteams",
      "path": "agentteams/fence_inject.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.backup",
        "agentteams.emit"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.fences": {
      "package": "agentteams",
      "path": "agentteams/fences.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.front_matter_merge",
        "agentteams.unfenced"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.fleet": {
      "package": "agentteams",
      "path": "agentteams/fleet.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup"
      ],
      "external": [],
      "repo_local": [
        "build_team"
      ]
    },
    "agentteams.framework_research": {
      "package": "agentteams",
      "path": "agentteams/framework_research.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks": {
      "package": "agentteams",
      "path": "agentteams/frameworks/__init__.py",
      "is_package": true,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks.agents_md": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/agents_md.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base",
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks.base": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/base.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks.claude": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/claude.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base",
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks.codex": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/codex.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.agents_md"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks.copilot_cli": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/copilot_cli.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base",
        "agentteams.frameworks.copilot_vscode",
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks.copilot_vscode": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/copilot_vscode.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base",
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks.goose": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/goose.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.capability_map",
        "agentteams.frameworks.base",
        "agentteams.frameworks.goose_docs",
        "agentteams.frameworks.goose_recipe_read",
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks.goose_docs": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/goose_docs.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.capability_hints"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks.goose_recipe_read": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/goose_recipe_read.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.frameworks.registry": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/registry.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.agents_md",
        "agentteams.frameworks.base",
        "agentteams.frameworks.claude",
        "agentteams.frameworks.codex",
        "agentteams.frameworks.copilot_cli",
        "agentteams.frameworks.copilot_vscode",
        "agentteams.frameworks.goose"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.front_matter_merge": {
      "package": "agentteams",
      "path": "agentteams/front_matter_merge.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.front_matter_reconcile": {
      "package": "agentteams",
      "path": "agentteams/front_matter_reconcile.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.front_matter_merge",
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.git_hooks": {
      "package": "agentteams",
      "path": "agentteams/git_hooks.py",
      "is_package": false,
      "imports_internal": [
        "agentteams",
        "agentteams.architecture",
        "agentteams.cli.artifacts",
        "agentteams.emit",
        "agentteams.errors",
        "agentteams.graph"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.goose_config": {
      "package": "agentteams",
      "path": "agentteams/goose_config.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.graph": {
      "package": "agentteams",
      "path": "agentteams/graph.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.graph_inputs",
        "agentteams.svg_render"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.graph_inputs": {
      "package": "agentteams",
      "path": "agentteams/graph_inputs.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.handoff_payloads": {
      "package": "agentteams",
      "path": "agentteams/handoff_payloads.py",
      "is_package": false,
      "imports_internal": [],
      "external": [
        "jsonschema"
      ],
      "repo_local": []
    },
    "agentteams.hooks_emit": {
      "package": "agentteams",
      "path": "agentteams/hooks_emit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.host_features": {
      "package": "agentteams",
      "path": "agentteams/host_features.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.ingest": {
      "package": "agentteams",
      "path": "agentteams/ingest.py",
      "is_package": false,
      "imports_internal": [
        "agentteams._utils"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.instructions_split": {
      "package": "agentteams",
      "path": "agentteams/instructions_split.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.integrity": {
      "package": "agentteams",
      "path": "agentteams/integrity.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.interop": {
      "package": "agentteams",
      "path": "agentteams/interop.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup",
        "agentteams.canonical",
        "agentteams.capability_map",
        "agentteams.fences",
        "agentteams.frameworks.base",
        "agentteams.frameworks.registry",
        "agentteams.interop_helpers",
        "agentteams.mcp_emit",
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.interop_helpers": {
      "package": "agentteams",
      "path": "agentteams/interop_helpers.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.canonical",
        "agentteams.yaml_frontmatter"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.liaison_logs": {
      "package": "agentteams",
      "path": "agentteams/liaison_logs.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.living_doc": {
      "package": "agentteams",
      "path": "agentteams/living_doc.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.man": {
      "package": "agentteams",
      "path": "agentteams/man.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": [
        "build_team"
      ]
    },
    "agentteams.manifest_format": {
      "package": "agentteams",
      "path": "agentteams/manifest_format.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.mcp_detect": {
      "package": "agentteams",
      "path": "agentteams/mcp_detect.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.mcp_emit": {
      "package": "agentteams",
      "path": "agentteams/mcp_emit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": [
        "jsonschema"
      ],
      "repo_local": []
    },
    "agentteams.memory_index": {
      "package": "agentteams",
      "path": "agentteams/memory_index.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.memory_index_incremental": {
      "package": "agentteams",
      "path": "agentteams/memory_index_incremental.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.memory_index"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.model_routing": {
      "package": "agentteams",
      "path": "agentteams/model_routing.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.multi_sync": {
      "package": "agentteams",
      "path": "agentteams/multi_sync.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.canonical",
        "agentteams.frameworks.registry",
        "agentteams.interop",
        "agentteams.sync_baseline",
        "agentteams.sync_classifier",
        "agentteams.sync_pin"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.output_plan": {
      "package": "agentteams",
      "path": "agentteams/output_plan.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.analyze"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.parallel_plan": {
      "package": "agentteams",
      "path": "agentteams/parallel_plan.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.plan_steps": {
      "package": "agentteams",
      "path": "agentteams/plan_steps.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.plan_steps_todo": {
      "package": "agentteams",
      "path": "agentteams/plan_steps_todo.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.pr_management": {
      "package": "agentteams",
      "path": "agentteams/pr_management.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.provenance": {
      "package": "agentteams",
      "path": "agentteams/provenance.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.rank_conformance": {
      "package": "agentteams",
      "path": "agentteams/rank_conformance.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.analyze",
        "agentteams.audit_types",
        "agentteams.capability_map"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.recipe_fields": {
      "package": "agentteams",
      "path": "agentteams/recipe_fields.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam": {
      "package": "agentteams",
      "path": "agentteams/redteam/__init__.py",
      "is_package": true,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.budget": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/budget.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.checks_report": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/checks_report.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.redteam.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.checks_static": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/checks_static.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.integrity",
        "agentteams.redteam.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.corpus": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/corpus.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.scan"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.coverage": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/coverage.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.redteam.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.cycle": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/cycle.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.redteam.checks_report",
        "agentteams.redteam.kev_correlation",
        "agentteams.redteam.realcopy",
        "agentteams.redteam.registry",
        "agentteams.redteam.report",
        "agentteams.redteam.runner",
        "agentteams.redteam.selfaudit"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.findings_ledger": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/findings_ledger.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.freshness": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/freshness.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.research.search"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.instantiate": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/instantiate.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.bridge_sources",
        "agentteams.frameworks.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.kev_correlation": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/kev_correlation.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.realcopy": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/realcopy.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.fleet"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.registry": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/registry.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.report": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/report.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.redteam.registry",
        "agentteams.redteam.runner",
        "agentteams.redteam.selfaudit"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.runner": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/runner.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.integrity",
        "agentteams.redteam.corpus",
        "agentteams.redteam.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.selfaudit": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/selfaudit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.redteam.checks_report",
        "agentteams.redteam.checks_static",
        "agentteams.redteam.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.redteam.sweep": {
      "package": "agentteams.redteam",
      "path": "agentteams/redteam/sweep.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.remediate": {
      "package": "agentteams",
      "path": "agentteams/remediate.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.render": {
      "package": "agentteams",
      "path": "agentteams/render.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.registry"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.research": {
      "package": "agentteams",
      "path": "agentteams/research/__init__.py",
      "is_package": true,
      "imports_internal": [
        "agentteams.research.backends",
        "agentteams.research.news",
        "agentteams.research.reputable",
        "agentteams.research.scholarly",
        "agentteams.research.search",
        "agentteams.research.verify"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.research.__main__": {
      "package": "agentteams.research",
      "path": "agentteams/research/__main__.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.research.browser",
        "agentteams.research.scholarly",
        "agentteams.research.search"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.research.backends": {
      "package": "agentteams.research",
      "path": "agentteams/research/backends.py",
      "is_package": false,
      "imports_internal": [],
      "external": [
        "httpx"
      ],
      "repo_local": []
    },
    "agentteams.research.browser": {
      "package": "agentteams.research",
      "path": "agentteams/research/browser.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.research.search"
      ],
      "external": [
        "playwright"
      ],
      "repo_local": []
    },
    "agentteams.research.cache": {
      "package": "agentteams.research",
      "path": "agentteams/research/cache.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.research.news": {
      "package": "agentteams.research",
      "path": "agentteams/research/news.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.research.reputable"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.research.reputable": {
      "package": "agentteams.research",
      "path": "agentteams/research/reputable.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.research.search"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.research.scholarly": {
      "package": "agentteams.research",
      "path": "agentteams/research/scholarly.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.research.cache"
      ],
      "external": [
        "httpx"
      ],
      "repo_local": []
    },
    "agentteams.research.search": {
      "package": "agentteams.research",
      "path": "agentteams/research/search.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.research.backends",
        "agentteams.research.cache"
      ],
      "external": [
        "httpx",
        "pypdf"
      ],
      "repo_local": []
    },
    "agentteams.research.verify": {
      "package": "agentteams.research",
      "path": "agentteams/research/verify.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.scan": {
      "package": "agentteams",
      "path": "agentteams/scan.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.schedule_emit": {
      "package": "agentteams",
      "path": "agentteams/schedule_emit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.security_feed_render": {
      "package": "agentteams",
      "path": "agentteams/security_feed_render.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.security_refs": {
      "package": "agentteams",
      "path": "agentteams/security_refs.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.cli.schema_cache",
        "agentteams.cli.security_gate",
        "agentteams.security_feed_render"
      ],
      "external": [
        "jsonschema"
      ],
      "repo_local": []
    },
    "agentteams.session_scan": {
      "package": "agentteams",
      "path": "agentteams/session_scan.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.plan_steps"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.stale_detector": {
      "package": "agentteams",
      "path": "agentteams/stale_detector.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup",
        "agentteams.bridge",
        "agentteams.drift",
        "agentteams.fleet"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.stale_remediate": {
      "package": "agentteams",
      "path": "agentteams/stale_remediate.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup",
        "agentteams.cli.commands",
        "agentteams.fleet",
        "agentteams.stale_detector"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.svg_render": {
      "package": "agentteams",
      "path": "agentteams/svg_render.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.sync_baseline": {
      "package": "agentteams",
      "path": "agentteams/sync_baseline.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.sync_classifier": {
      "package": "agentteams",
      "path": "agentteams/sync_classifier.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.front_matter_merge"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.sync_pin": {
      "package": "agentteams",
      "path": "agentteams/sync_pin.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.team_package": {
      "package": "agentteams",
      "path": "agentteams/team_package.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.bridge",
        "agentteams.canonical",
        "agentteams.interop"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.template_pins": {
      "package": "agentteams",
      "path": "agentteams/template_pins.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.errors",
        "agentteams.render"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.toml_write": {
      "package": "agentteams",
      "path": "agentteams/toml_write.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.tool_metadata_catalog": {
      "package": "agentteams",
      "path": "agentteams/tool_metadata_catalog.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.unfenced": {
      "package": "agentteams",
      "path": "agentteams/unfenced.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.front_matter_merge"
      ],
      "external": [],
      "repo_local": []
    },
    "agentteams.update_report": {
      "package": "agentteams",
      "path": "agentteams/update_report.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.vscode_tasks": {
      "package": "agentteams",
      "path": "agentteams/vscode_tasks.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    },
    "agentteams.yaml_frontmatter": {
      "package": "agentteams",
      "path": "agentteams/yaml_frontmatter.py",
      "is_package": false,
      "imports_internal": [],
      "external": [],
      "repo_local": []
    }
  },
  "package_edges": [
    {
      "source": "agentteams",
      "target": "agentteams.cli"
    },
    {
      "source": "agentteams",
      "target": "agentteams.enrich"
    },
    {
      "source": "agentteams",
      "target": "agentteams.frameworks"
    },
    {
      "source": "agentteams",
      "target": "agentteams.research"
    },
    {
      "source": "agentteams.cli",
      "target": "agentteams"
    },
    {
      "source": "agentteams.cli",
      "target": "agentteams.frameworks"
    },
    {
      "source": "agentteams.cli",
      "target": "agentteams.redteam"
    },
    {
      "source": "agentteams.enrich",
      "target": "agentteams"
    },
    {
      "source": "agentteams.frameworks",
      "target": "agentteams"
    },
    {
      "source": "agentteams.redteam",
      "target": "agentteams"
    },
    {
      "source": "agentteams.redteam",
      "target": "agentteams.frameworks"
    },
    {
      "source": "agentteams.redteam",
      "target": "agentteams.research"
    }
  ],
  "module_edges": [
    {
      "source": "agentteams.analyze",
      "target": "agentteams._utils"
    },
    {
      "source": "agentteams.analyze",
      "target": "agentteams.host_features"
    },
    {
      "source": "agentteams.analyze",
      "target": "agentteams.manifest_format"
    },
    {
      "source": "agentteams.analyze",
      "target": "agentteams.mcp_detect"
    },
    {
      "source": "agentteams.analyze",
      "target": "agentteams.mcp_emit"
    },
    {
      "source": "agentteams.analyze",
      "target": "agentteams.output_plan"
    },
    {
      "source": "agentteams.analyze",
      "target": "agentteams.recipe_fields"
    },
    {
      "source": "agentteams.analyze",
      "target": "agentteams.tool_metadata_catalog"
    },
    {
      "source": "agentteams.architecture",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.architecture",
      "target": "agentteams.svg_render"
    },
    {
      "source": "agentteams.audit",
      "target": "agentteams.audit_agent_contract"
    },
    {
      "source": "agentteams.audit",
      "target": "agentteams.audit_types"
    },
    {
      "source": "agentteams.audit",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.audit",
      "target": "agentteams.living_doc"
    },
    {
      "source": "agentteams.audit_agent_contract",
      "target": "agentteams.audit_types"
    },
    {
      "source": "agentteams.audit_types",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.backup",
      "target": "agentteams"
    },
    {
      "source": "agentteams.backup",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.backup",
      "target": "agentteams.liaison_logs"
    },
    {
      "source": "agentteams.behavioral_drift",
      "target": "agentteams.handoff_payloads"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.bridge_pair_docs"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.bridge_skills"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.bridge_sources"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.bridge_subagents"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.bridge_subagents_goose"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.canonical"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.capability_hints"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.frameworks.goose"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.hooks_emit"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.instructions_split"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.interop"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.parallel_plan"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.plan_steps_todo"
    },
    {
      "source": "agentteams.bridge",
      "target": "agentteams.schedule_emit"
    },
    {
      "source": "agentteams.bridge_pair_docs",
      "target": "agentteams.canonical"
    },
    {
      "source": "agentteams.bridge_sources",
      "target": "agentteams.canonical"
    },
    {
      "source": "agentteams.bridge_sources",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.bridge_subagents",
      "target": "agentteams.frameworks.claude"
    },
    {
      "source": "agentteams.bridge_subagents_goose",
      "target": "agentteams.bridge_subagents"
    },
    {
      "source": "agentteams.bridge_subagents_goose",
      "target": "agentteams.frameworks.goose"
    },
    {
      "source": "agentteams.canonical",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.canonical",
      "target": "agentteams.interop"
    },
    {
      "source": "agentteams.canonical",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.capability_map",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.baseline"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.cli.commands"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.cli.generate"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.cli.goose_switch"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.cli.json_mode"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.cli.package_switch"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.cli.parser"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.cli.recipe_check"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.cli.render_pipeline"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.cli.sync_switch"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.fence_inject"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.fleet"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.frameworks.goose"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.git_hooks"
    },
    {
      "source": "agentteams.cli.app",
      "target": "agentteams.host_features"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.cli.code_index_artifacts"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.cli.grants"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.cli.schema_cache"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.codex_mcp_emit"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.drift"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.errors"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.eval_suite"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.fences"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.frameworks.claude"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.host_features"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.mcp_emit"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.memory_index"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.memory_index_incremental"
    },
    {
      "source": "agentteams.cli.artifacts",
      "target": "agentteams.model_routing"
    },
    {
      "source": "agentteams.cli.backup_switch",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.cli.code_index_artifacts",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.cli.code_index_artifacts",
      "target": "agentteams.cli.schema_cache"
    },
    {
      "source": "agentteams.cli.code_index_artifacts",
      "target": "agentteams.code_index"
    },
    {
      "source": "agentteams.cli.code_index_artifacts",
      "target": "agentteams.code_sources"
    },
    {
      "source": "agentteams.cli.code_index_artifacts",
      "target": "agentteams.errors"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.bridge"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.canonical"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.cli.grants"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.cli.security_gate"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.convert"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.drift"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.integrity"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.interop"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.redteam.cycle"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.redteam.freshness"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.research"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.security_refs"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.stale_detector"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.stale_remediate"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.sync_baseline"
    },
    {
      "source": "agentteams.cli.commands",
      "target": "agentteams.sync_classifier"
    },
    {
      "source": "agentteams.cli.exit_codes",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.ai_bad_habits"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.analyze"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.audit"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.cli.artifacts"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.cli.exit_codes"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.cli.json_mode"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.cli.output_target"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.cli.post_emit_checks"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.cli.render_pipeline"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.cli.security_gate"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.cli.standalone_modes"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.drift"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.enrich"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.errors"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.framework_research"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.front_matter_reconcile"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.git_hooks"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.graph"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.ingest"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.integrity"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.liaison_logs"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.render"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.security_refs"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.template_pins"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.update_report"
    },
    {
      "source": "agentteams.cli.goose_switch",
      "target": "agentteams.goose_config"
    },
    {
      "source": "agentteams.cli.grants",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.cli.grants",
      "target": "agentteams.cli.decision_log"
    },
    {
      "source": "agentteams.cli.grants",
      "target": "agentteams.cli.signed_ledger"
    },
    {
      "source": "agentteams.cli.output_target",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.cli.package_switch",
      "target": "agentteams.cli.security_gate"
    },
    {
      "source": "agentteams.cli.package_switch",
      "target": "agentteams.security_refs"
    },
    {
      "source": "agentteams.cli.package_switch",
      "target": "agentteams.team_package"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams.cli.backup_switch"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams.cli.fleet_switch"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams.cli.goose_switch"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams.cli.package_switch"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams.cli.parser_validate"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams.cli.sync_switch"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.cli.post_emit_checks",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.cli.post_emit_checks",
      "target": "agentteams.scan"
    },
    {
      "source": "agentteams.cli.recipe_check",
      "target": "agentteams.frameworks.goose"
    },
    {
      "source": "agentteams.cli.render_pipeline",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.cli.render_pipeline",
      "target": "agentteams.frameworks.agents_md"
    },
    {
      "source": "agentteams.cli.render_pipeline",
      "target": "agentteams.frameworks.base"
    },
    {
      "source": "agentteams.cli.render_pipeline",
      "target": "agentteams.frameworks.claude"
    },
    {
      "source": "agentteams.cli.render_pipeline",
      "target": "agentteams.frameworks.copilot_cli"
    },
    {
      "source": "agentteams.cli.render_pipeline",
      "target": "agentteams.frameworks.copilot_vscode"
    },
    {
      "source": "agentteams.cli.render_pipeline",
      "target": "agentteams.frameworks.goose"
    },
    {
      "source": "agentteams.cli.render_pipeline",
      "target": "agentteams.graph"
    },
    {
      "source": "agentteams.cli.render_pipeline",
      "target": "agentteams.render"
    },
    {
      "source": "agentteams.cli.render_pipeline",
      "target": "agentteams.vscode_tasks"
    },
    {
      "source": "agentteams.cli.schema_cache",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.cli.security_gate",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.cli.security_gate",
      "target": "agentteams.cli.decision_log"
    },
    {
      "source": "agentteams.cli.standalone_modes",
      "target": "agentteams.audit_types"
    },
    {
      "source": "agentteams.cli.standalone_modes",
      "target": "agentteams.budget"
    },
    {
      "source": "agentteams.cli.standalone_modes",
      "target": "agentteams.cli.artifacts"
    },
    {
      "source": "agentteams.cli.standalone_modes",
      "target": "agentteams.cli.security_gate"
    },
    {
      "source": "agentteams.cli.standalone_modes",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.cli.standalone_modes",
      "target": "agentteams.rank_conformance"
    },
    {
      "source": "agentteams.cli.standalone_modes",
      "target": "agentteams.scan"
    },
    {
      "source": "agentteams.cli.standalone_modes",
      "target": "agentteams.template_pins"
    },
    {
      "source": "agentteams.cli.sync_switch",
      "target": "agentteams.multi_sync"
    },
    {
      "source": "agentteams.code_sources",
      "target": "agentteams.code_index"
    },
    {
      "source": "agentteams.codex_mcp_emit",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.codex_mcp_emit",
      "target": "agentteams.mcp_emit"
    },
    {
      "source": "agentteams.codex_mcp_emit",
      "target": "agentteams.toml_write"
    },
    {
      "source": "agentteams.convert",
      "target": "agentteams.frameworks.base"
    },
    {
      "source": "agentteams.convert",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.drift",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.emit",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.emit",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.emit",
      "target": "agentteams.drift"
    },
    {
      "source": "agentteams.emit",
      "target": "agentteams.fence_inject"
    },
    {
      "source": "agentteams.emit",
      "target": "agentteams.fences"
    },
    {
      "source": "agentteams.enrich",
      "target": "agentteams.enrich._audit"
    },
    {
      "source": "agentteams.enrich",
      "target": "agentteams.enrich._enrich"
    },
    {
      "source": "agentteams.enrich",
      "target": "agentteams.enrich._models"
    },
    {
      "source": "agentteams.enrich",
      "target": "agentteams.enrich._tools"
    },
    {
      "source": "agentteams.enrich._audit",
      "target": "agentteams.enrich._fills"
    },
    {
      "source": "agentteams.enrich._audit",
      "target": "agentteams.enrich._models"
    },
    {
      "source": "agentteams.enrich._audit",
      "target": "agentteams.enrich._tools"
    },
    {
      "source": "agentteams.enrich._audit",
      "target": "agentteams.tool_metadata_catalog"
    },
    {
      "source": "agentteams.enrich._enrich",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.enrich._enrich",
      "target": "agentteams.enrich._fills"
    },
    {
      "source": "agentteams.enrich._enrich",
      "target": "agentteams.enrich._models"
    },
    {
      "source": "agentteams.enrich._enrich",
      "target": "agentteams.enrich._notebooks"
    },
    {
      "source": "agentteams.enrich._enrich",
      "target": "agentteams.enrich._tools"
    },
    {
      "source": "agentteams.enrich._notebooks",
      "target": "agentteams.enrich._models"
    },
    {
      "source": "agentteams.enrich._notebooks",
      "target": "agentteams.enrich._tools"
    },
    {
      "source": "agentteams.enrich._notebooks",
      "target": "agentteams.tool_metadata_catalog"
    },
    {
      "source": "agentteams.enrich._tools",
      "target": "agentteams.tool_metadata_catalog"
    },
    {
      "source": "agentteams.fence_inject",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.fence_inject",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.fence_inject",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.fences",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.fences",
      "target": "agentteams.front_matter_merge"
    },
    {
      "source": "agentteams.fences",
      "target": "agentteams.unfenced"
    },
    {
      "source": "agentteams.fleet",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.frameworks.agents_md",
      "target": "agentteams.frameworks.base"
    },
    {
      "source": "agentteams.frameworks.agents_md",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.frameworks.base",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.frameworks.claude",
      "target": "agentteams.frameworks.base"
    },
    {
      "source": "agentteams.frameworks.claude",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.frameworks.codex",
      "target": "agentteams.frameworks.agents_md"
    },
    {
      "source": "agentteams.frameworks.copilot_cli",
      "target": "agentteams.frameworks.base"
    },
    {
      "source": "agentteams.frameworks.copilot_cli",
      "target": "agentteams.frameworks.copilot_vscode"
    },
    {
      "source": "agentteams.frameworks.copilot_cli",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.frameworks.copilot_vscode",
      "target": "agentteams.frameworks.base"
    },
    {
      "source": "agentteams.frameworks.copilot_vscode",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.frameworks.goose",
      "target": "agentteams.capability_map"
    },
    {
      "source": "agentteams.frameworks.goose",
      "target": "agentteams.frameworks.base"
    },
    {
      "source": "agentteams.frameworks.goose",
      "target": "agentteams.frameworks.goose_docs"
    },
    {
      "source": "agentteams.frameworks.goose",
      "target": "agentteams.frameworks.goose_recipe_read"
    },
    {
      "source": "agentteams.frameworks.goose",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.frameworks.goose_docs",
      "target": "agentteams.capability_hints"
    },
    {
      "source": "agentteams.frameworks.registry",
      "target": "agentteams.frameworks.agents_md"
    },
    {
      "source": "agentteams.frameworks.registry",
      "target": "agentteams.frameworks.base"
    },
    {
      "source": "agentteams.frameworks.registry",
      "target": "agentteams.frameworks.claude"
    },
    {
      "source": "agentteams.frameworks.registry",
      "target": "agentteams.frameworks.codex"
    },
    {
      "source": "agentteams.frameworks.registry",
      "target": "agentteams.frameworks.copilot_cli"
    },
    {
      "source": "agentteams.frameworks.registry",
      "target": "agentteams.frameworks.copilot_vscode"
    },
    {
      "source": "agentteams.frameworks.registry",
      "target": "agentteams.frameworks.goose"
    },
    {
      "source": "agentteams.front_matter_reconcile",
      "target": "agentteams.front_matter_merge"
    },
    {
      "source": "agentteams.front_matter_reconcile",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.git_hooks",
      "target": "agentteams"
    },
    {
      "source": "agentteams.git_hooks",
      "target": "agentteams.architecture"
    },
    {
      "source": "agentteams.git_hooks",
      "target": "agentteams.cli.artifacts"
    },
    {
      "source": "agentteams.git_hooks",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.git_hooks",
      "target": "agentteams.errors"
    },
    {
      "source": "agentteams.git_hooks",
      "target": "agentteams.graph"
    },
    {
      "source": "agentteams.graph",
      "target": "agentteams.graph_inputs"
    },
    {
      "source": "agentteams.graph",
      "target": "agentteams.svg_render"
    },
    {
      "source": "agentteams.graph_inputs",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.hooks_emit",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.ingest",
      "target": "agentteams._utils"
    },
    {
      "source": "agentteams.interop",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.interop",
      "target": "agentteams.canonical"
    },
    {
      "source": "agentteams.interop",
      "target": "agentteams.capability_map"
    },
    {
      "source": "agentteams.interop",
      "target": "agentteams.fences"
    },
    {
      "source": "agentteams.interop",
      "target": "agentteams.frameworks.base"
    },
    {
      "source": "agentteams.interop",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.interop",
      "target": "agentteams.interop_helpers"
    },
    {
      "source": "agentteams.interop",
      "target": "agentteams.mcp_emit"
    },
    {
      "source": "agentteams.interop",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.interop_helpers",
      "target": "agentteams.canonical"
    },
    {
      "source": "agentteams.interop_helpers",
      "target": "agentteams.yaml_frontmatter"
    },
    {
      "source": "agentteams.liaison_logs",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.manifest_format",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.mcp_emit",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.memory_index_incremental",
      "target": "agentteams.memory_index"
    },
    {
      "source": "agentteams.multi_sync",
      "target": "agentteams.canonical"
    },
    {
      "source": "agentteams.multi_sync",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.multi_sync",
      "target": "agentteams.interop"
    },
    {
      "source": "agentteams.multi_sync",
      "target": "agentteams.sync_baseline"
    },
    {
      "source": "agentteams.multi_sync",
      "target": "agentteams.sync_classifier"
    },
    {
      "source": "agentteams.multi_sync",
      "target": "agentteams.sync_pin"
    },
    {
      "source": "agentteams.output_plan",
      "target": "agentteams.analyze"
    },
    {
      "source": "agentteams.plan_steps_todo",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.rank_conformance",
      "target": "agentteams.analyze"
    },
    {
      "source": "agentteams.rank_conformance",
      "target": "agentteams.audit_types"
    },
    {
      "source": "agentteams.rank_conformance",
      "target": "agentteams.capability_map"
    },
    {
      "source": "agentteams.redteam.checks_report",
      "target": "agentteams.redteam.registry"
    },
    {
      "source": "agentteams.redteam.checks_static",
      "target": "agentteams.integrity"
    },
    {
      "source": "agentteams.redteam.checks_static",
      "target": "agentteams.redteam.registry"
    },
    {
      "source": "agentteams.redteam.corpus",
      "target": "agentteams.scan"
    },
    {
      "source": "agentteams.redteam.coverage",
      "target": "agentteams.redteam.registry"
    },
    {
      "source": "agentteams.redteam.cycle",
      "target": "agentteams.redteam.checks_report"
    },
    {
      "source": "agentteams.redteam.cycle",
      "target": "agentteams.redteam.kev_correlation"
    },
    {
      "source": "agentteams.redteam.cycle",
      "target": "agentteams.redteam.realcopy"
    },
    {
      "source": "agentteams.redteam.cycle",
      "target": "agentteams.redteam.registry"
    },
    {
      "source": "agentteams.redteam.cycle",
      "target": "agentteams.redteam.report"
    },
    {
      "source": "agentteams.redteam.cycle",
      "target": "agentteams.redteam.runner"
    },
    {
      "source": "agentteams.redteam.cycle",
      "target": "agentteams.redteam.selfaudit"
    },
    {
      "source": "agentteams.redteam.findings_ledger",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.redteam.freshness",
      "target": "agentteams.research.search"
    },
    {
      "source": "agentteams.redteam.instantiate",
      "target": "agentteams.bridge_sources"
    },
    {
      "source": "agentteams.redteam.instantiate",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.redteam.realcopy",
      "target": "agentteams.fleet"
    },
    {
      "source": "agentteams.redteam.report",
      "target": "agentteams.redteam.registry"
    },
    {
      "source": "agentteams.redteam.report",
      "target": "agentteams.redteam.runner"
    },
    {
      "source": "agentteams.redteam.report",
      "target": "agentteams.redteam.selfaudit"
    },
    {
      "source": "agentteams.redteam.runner",
      "target": "agentteams.integrity"
    },
    {
      "source": "agentteams.redteam.runner",
      "target": "agentteams.redteam.corpus"
    },
    {
      "source": "agentteams.redteam.runner",
      "target": "agentteams.redteam.registry"
    },
    {
      "source": "agentteams.redteam.selfaudit",
      "target": "agentteams.redteam.checks_report"
    },
    {
      "source": "agentteams.redteam.selfaudit",
      "target": "agentteams.redteam.checks_static"
    },
    {
      "source": "agentteams.redteam.selfaudit",
      "target": "agentteams.redteam.registry"
    },
    {
      "source": "agentteams.redteam.sweep",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.render",
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.research",
      "target": "agentteams.research.backends"
    },
    {
      "source": "agentteams.research",
      "target": "agentteams.research.news"
    },
    {
      "source": "agentteams.research",
      "target": "agentteams.research.reputable"
    },
    {
      "source": "agentteams.research",
      "target": "agentteams.research.scholarly"
    },
    {
      "source": "agentteams.research",
      "target": "agentteams.research.search"
    },
    {
      "source": "agentteams.research",
      "target": "agentteams.research.verify"
    },
    {
      "source": "agentteams.research.__main__",
      "target": "agentteams.research.browser"
    },
    {
      "source": "agentteams.research.__main__",
      "target": "agentteams.research.scholarly"
    },
    {
      "source": "agentteams.research.__main__",
      "target": "agentteams.research.search"
    },
    {
      "source": "agentteams.research.browser",
      "target": "agentteams.research.search"
    },
    {
      "source": "agentteams.research.news",
      "target": "agentteams.research.reputable"
    },
    {
      "source": "agentteams.research.reputable",
      "target": "agentteams.research.search"
    },
    {
      "source": "agentteams.research.scholarly",
      "target": "agentteams.research.cache"
    },
    {
      "source": "agentteams.research.search",
      "target": "agentteams.research.backends"
    },
    {
      "source": "agentteams.research.search",
      "target": "agentteams.research.cache"
    },
    {
      "source": "agentteams.scan",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.schedule_emit",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.security_refs",
      "target": "agentteams.cli.schema_cache"
    },
    {
      "source": "agentteams.security_refs",
      "target": "agentteams.cli.security_gate"
    },
    {
      "source": "agentteams.security_refs",
      "target": "agentteams.security_feed_render"
    },
    {
      "source": "agentteams.session_scan",
      "target": "agentteams.plan_steps"
    },
    {
      "source": "agentteams.stale_detector",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.stale_detector",
      "target": "agentteams.bridge"
    },
    {
      "source": "agentteams.stale_detector",
      "target": "agentteams.drift"
    },
    {
      "source": "agentteams.stale_detector",
      "target": "agentteams.fleet"
    },
    {
      "source": "agentteams.stale_remediate",
      "target": "agentteams.backup"
    },
    {
      "source": "agentteams.stale_remediate",
      "target": "agentteams.cli.commands"
    },
    {
      "source": "agentteams.stale_remediate",
      "target": "agentteams.fleet"
    },
    {
      "source": "agentteams.stale_remediate",
      "target": "agentteams.stale_detector"
    },
    {
      "source": "agentteams.sync_baseline",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.sync_classifier",
      "target": "agentteams.front_matter_merge"
    },
    {
      "source": "agentteams.sync_pin",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.team_package",
      "target": "agentteams.atomicio"
    },
    {
      "source": "agentteams.team_package",
      "target": "agentteams.bridge"
    },
    {
      "source": "agentteams.team_package",
      "target": "agentteams.canonical"
    },
    {
      "source": "agentteams.team_package",
      "target": "agentteams.interop"
    },
    {
      "source": "agentteams.template_pins",
      "target": "agentteams.errors"
    },
    {
      "source": "agentteams.template_pins",
      "target": "agentteams.render"
    },
    {
      "source": "agentteams.unfenced",
      "target": "agentteams.front_matter_merge"
    }
  ],
  "external_dependencies": [
    "httpx",
    "jsonschema",
    "playwright",
    "pypdf",
    "referencing",
    "yaml"
  ],
  "repo_local_dependencies": [
    "build_team"
  ]
}
```
<!-- AGENTTEAMS:END content -->
