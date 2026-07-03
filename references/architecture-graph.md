<!-- AGENTTEAMS:BEGIN content v=1 -->
# agentteams — Repository Architecture Map

> **Auto-generated.** Regenerated on every commit that touches the `agentteams` package. Do not edit manually — changes will be overwritten.

- Modules mapped: **86**
- Packages: **5**
- Internal import edges: **144**
- Distinct external dependencies: **2**

---

## Package Dependency Diagram

Inter-package import dependencies (module-level detail in the tables below).

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
    agentteams --> agentteams_cli
    agentteams --> agentteams_frameworks
    agentteams_cli --> agentteams
    agentteams_cli --> agentteams_frameworks
    agentteams_frameworks --> agentteams
```

---

## Packages

| Package | Modules | Depends on |
| --- | --- | --- |
| `agentteams` | 61 | `agentteams.cli`, `agentteams.frameworks` |
| `agentteams.cli` | 10 | `agentteams`, `agentteams.frameworks` |
| `agentteams.enrich` | 6 | — |
| `agentteams.eval_adapters` | 2 | — |
| `agentteams.frameworks` | 7 | `agentteams` |

---

## Module Dependency Table

| Module | Imports (internal) | Imported by |
| --- | --- | --- |
| `agentteams` | — | `agentteams.backup`, `agentteams.cli.artifacts`, `agentteams.cli.parser`, `agentteams.enrich`, `agentteams.git_hooks` |
| `agentteams._utils` | — | `agentteams.analyze`, `agentteams.graph`, `agentteams.ingest` |
| `agentteams.advisory` | — | — |
| `agentteams.ai_bad_habits` | — | `agentteams.cli.generate` |
| `agentteams.analyze` | `agentteams._utils`, `agentteams.manifest_format`, `agentteams.mcp_detect`, `agentteams.output_plan`, `agentteams.recipe_fields` | `agentteams.cli.generate`, `agentteams.output_plan` |
| `agentteams.architecture` | — | `agentteams.git_hooks` |
| `agentteams.atomicio` | — | `agentteams.backup`, `agentteams.emit`, `agentteams.fence_inject`, `agentteams.fences`, `agentteams.hooks_emit`, `agentteams.mcp_emit`, `agentteams.schedule_emit` |
| `agentteams.audit` | — | `agentteams.cli.generate` |
| `agentteams.backup` | `agentteams`, `agentteams.atomicio`, `agentteams.liaison_logs` | `agentteams.bridge`, `agentteams.emit` |
| `agentteams.baseline` | — | `agentteams.cli.app` |
| `agentteams.behavioral_drift` | `agentteams.handoff_payloads` | — |
| `agentteams.bridge` | `agentteams.backup`, `agentteams.bridge_sources`, `agentteams.bridge_subagents`, `agentteams.bridge_subagents_goose`, `agentteams.frameworks.goose`, `agentteams.hooks_emit`, `agentteams.instructions_split`, `agentteams.interop`, `agentteams.parallel_plan`, `agentteams.plan_steps_todo`, `agentteams.schedule_emit` | `agentteams.cli.commands`, `agentteams.stale_detector` |
| `agentteams.bridge_sources` | — | `agentteams.bridge` |
| `agentteams.bridge_subagents` | — | `agentteams.bridge`, `agentteams.bridge_subagents_goose` |
| `agentteams.bridge_subagents_goose` | `agentteams.bridge_subagents`, `agentteams.frameworks.goose` | `agentteams.bridge` |
| `agentteams.budget` | — | `agentteams.cli.generate` |
| `agentteams.cli` | — | — |
| `agentteams.cli.app` | `agentteams.baseline`, `agentteams.cli.commands`, `agentteams.cli.generate`, `agentteams.cli.goose_switch`, `agentteams.cli.parser`, `agentteams.cli.recipe_check`, `agentteams.cli.render_pipeline`, `agentteams.fence_inject`, `agentteams.fleet`, `agentteams.frameworks.goose`, `agentteams.git_hooks`, `agentteams.host_features` | — |
| `agentteams.cli.artifacts` | `agentteams`, `agentteams.drift`, `agentteams.errors`, `agentteams.eval_suite`, `agentteams.mcp_emit`, `agentteams.memory_index`, `agentteams.memory_index_incremental`, `agentteams.model_routing` | `agentteams.cli.generate` |
| `agentteams.cli.commands` | `agentteams.bridge`, `agentteams.cli.security_gate`, `agentteams.convert`, `agentteams.drift`, `agentteams.emit`, `agentteams.frameworks.registry`, `agentteams.interop`, `agentteams.security_refs`, `agentteams.stale_detector`, `agentteams.stale_remediate` | `agentteams.cli.app`, `agentteams.stale_remediate` |
| `agentteams.cli.generate` | `agentteams.ai_bad_habits`, `agentteams.analyze`, `agentteams.audit`, `agentteams.budget`, `agentteams.cli.artifacts`, `agentteams.cli.render_pipeline`, `agentteams.cli.security_gate`, `agentteams.drift`, `agentteams.emit`, `agentteams.enrich`, `agentteams.errors`, `agentteams.framework_research`, `agentteams.frameworks.registry`, `agentteams.git_hooks`, `agentteams.graph`, `agentteams.ingest`, `agentteams.liaison_logs`, `agentteams.render`, `agentteams.scan`, `agentteams.security_refs` | `agentteams.cli.app` |
| `agentteams.cli.goose_switch` | `agentteams.goose_config` | `agentteams.cli.app`, `agentteams.cli.parser` |
| `agentteams.cli.parser` | `agentteams`, `agentteams.cli.goose_switch`, `agentteams.cli.parser_validate`, `agentteams.emit`, `agentteams.frameworks.registry` | `agentteams.cli.app` |
| `agentteams.cli.parser_validate` | — | `agentteams.cli.parser` |
| `agentteams.cli.recipe_check` | `agentteams.frameworks.goose` | `agentteams.cli.app` |
| `agentteams.cli.render_pipeline` | `agentteams.emit`, `agentteams.frameworks.agents_md`, `agentteams.frameworks.base`, `agentteams.frameworks.claude`, `agentteams.frameworks.copilot_cli`, `agentteams.frameworks.copilot_vscode`, `agentteams.frameworks.goose`, `agentteams.graph`, `agentteams.render`, `agentteams.vscode_tasks` | `agentteams.cli.app`, `agentteams.cli.generate` |
| `agentteams.cli.security_gate` | — | `agentteams.cli.commands`, `agentteams.cli.generate` |
| `agentteams.convert` | `agentteams.frameworks.base`, `agentteams.frameworks.registry` | `agentteams.cli.commands` |
| `agentteams.drift` | `agentteams.emit` | `agentteams.cli.artifacts`, `agentteams.cli.commands`, `agentteams.cli.generate`, `agentteams.stale_detector` |
| `agentteams.emit` | `agentteams.atomicio`, `agentteams.backup`, `agentteams.fence_inject`, `agentteams.fences` | `agentteams.cli.commands`, `agentteams.cli.generate`, `agentteams.cli.parser`, `agentteams.cli.render_pipeline`, `agentteams.drift`, `agentteams.fence_inject`, `agentteams.git_hooks` |
| `agentteams.enrich` | `agentteams` | `agentteams.cli.generate` |
| `agentteams.enrich._audit` | `agentteams.enrich._fills`, `agentteams.enrich._models`, `agentteams.enrich._tools` | — |
| `agentteams.enrich._enrich` | `agentteams.enrich._fills`, `agentteams.enrich._models`, `agentteams.enrich._notebooks`, `agentteams.enrich._tools` | — |
| `agentteams.enrich._fills` | — | `agentteams.enrich._audit`, `agentteams.enrich._enrich` |
| `agentteams.enrich._models` | — | `agentteams.enrich._audit`, `agentteams.enrich._enrich`, `agentteams.enrich._notebooks` |
| `agentteams.enrich._notebooks` | `agentteams.enrich._models`, `agentteams.enrich._tools` | `agentteams.enrich._enrich` |
| `agentteams.enrich._tools` | — | `agentteams.enrich._audit`, `agentteams.enrich._enrich`, `agentteams.enrich._notebooks` |
| `agentteams.errors` | — | `agentteams.cli.artifacts`, `agentteams.cli.generate` |
| `agentteams.eval_adapters` | — | — |
| `agentteams.eval_adapters.inspect_ai` | — | — |
| `agentteams.eval_adapters.openai_evals` | — | — |
| `agentteams.eval_suite` | — | `agentteams.cli.artifacts` |
| `agentteams.fence_inject` | `agentteams.atomicio`, `agentteams.emit` | `agentteams.cli.app`, `agentteams.emit` |
| `agentteams.fences` | `agentteams.atomicio` | `agentteams.emit` |
| `agentteams.fleet` | — | `agentteams.cli.app`, `agentteams.stale_detector`, `agentteams.stale_remediate` |
| `agentteams.framework_research` | — | `agentteams.cli.generate` |
| `agentteams.frameworks` | — | — |
| `agentteams.frameworks.agents_md` | `agentteams.frameworks.base`, `agentteams.yaml_frontmatter` | `agentteams.cli.render_pipeline`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.base` | `agentteams.yaml_frontmatter` | `agentteams.cli.render_pipeline`, `agentteams.convert`, `agentteams.frameworks.agents_md`, `agentteams.frameworks.claude`, `agentteams.frameworks.copilot_cli`, `agentteams.frameworks.copilot_vscode`, `agentteams.frameworks.goose`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.claude` | `agentteams.frameworks.base`, `agentteams.yaml_frontmatter` | `agentteams.cli.render_pipeline`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.copilot_cli` | `agentteams.frameworks.base` | `agentteams.cli.render_pipeline`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.copilot_vscode` | `agentteams.frameworks.base`, `agentteams.yaml_frontmatter` | `agentteams.cli.render_pipeline`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.goose` | `agentteams.frameworks.base`, `agentteams.yaml_frontmatter` | `agentteams.bridge`, `agentteams.bridge_subagents_goose`, `agentteams.cli.app`, `agentteams.cli.recipe_check`, `agentteams.cli.render_pipeline`, `agentteams.frameworks.registry` |
| `agentteams.frameworks.registry` | `agentteams.frameworks.agents_md`, `agentteams.frameworks.base`, `agentteams.frameworks.claude`, `agentteams.frameworks.copilot_cli`, `agentteams.frameworks.copilot_vscode`, `agentteams.frameworks.goose` | `agentteams.cli.commands`, `agentteams.cli.generate`, `agentteams.cli.parser`, `agentteams.convert`, `agentteams.interop` |
| `agentteams.git_hooks` | `agentteams`, `agentteams.architecture`, `agentteams.emit`, `agentteams.graph` | `agentteams.cli.app`, `agentteams.cli.generate` |
| `agentteams.goose_config` | — | `agentteams.cli.goose_switch` |
| `agentteams.graph` | `agentteams._utils` | `agentteams.cli.generate`, `agentteams.cli.render_pipeline`, `agentteams.git_hooks` |
| `agentteams.handoff_payloads` | — | `agentteams.behavioral_drift` |
| `agentteams.hooks_emit` | `agentteams.atomicio` | `agentteams.bridge` |
| `agentteams.host_features` | — | `agentteams.cli.app` |
| `agentteams.ingest` | `agentteams._utils` | `agentteams.cli.generate` |
| `agentteams.instructions_split` | — | `agentteams.bridge` |
| `agentteams.interop` | `agentteams.frameworks.registry`, `agentteams.yaml_frontmatter` | `agentteams.bridge`, `agentteams.cli.commands` |
| `agentteams.liaison_logs` | — | `agentteams.backup`, `agentteams.cli.generate` |
| `agentteams.man` | — | — |
| `agentteams.manifest_format` | — | `agentteams.analyze` |
| `agentteams.mcp_detect` | — | `agentteams.analyze` |
| `agentteams.mcp_emit` | `agentteams.atomicio` | `agentteams.cli.artifacts` |
| `agentteams.memory_index` | — | `agentteams.cli.artifacts`, `agentteams.memory_index_incremental` |
| `agentteams.memory_index_incremental` | `agentteams.memory_index` | `agentteams.cli.artifacts` |
| `agentteams.model_routing` | — | `agentteams.cli.artifacts` |
| `agentteams.output_plan` | `agentteams.analyze` | `agentteams.analyze` |
| `agentteams.parallel_plan` | — | `agentteams.bridge` |
| `agentteams.plan_steps` | — | — |
| `agentteams.plan_steps_todo` | — | `agentteams.bridge` |
| `agentteams.pr_management` | — | — |
| `agentteams.recipe_fields` | — | `agentteams.analyze` |
| `agentteams.remediate` | — | — |
| `agentteams.render` | — | `agentteams.cli.generate`, `agentteams.cli.render_pipeline` |
| `agentteams.scan` | — | `agentteams.cli.generate` |
| `agentteams.schedule_emit` | `agentteams.atomicio` | `agentteams.bridge` |
| `agentteams.security_refs` | — | `agentteams.cli.commands`, `agentteams.cli.generate` |
| `agentteams.stale_detector` | `agentteams.bridge`, `agentteams.drift`, `agentteams.fleet` | `agentteams.cli.commands`, `agentteams.stale_remediate` |
| `agentteams.stale_remediate` | `agentteams.cli.commands`, `agentteams.fleet`, `agentteams.stale_detector` | `agentteams.cli.commands` |
| `agentteams.vscode_tasks` | — | `agentteams.cli.render_pipeline` |
| `agentteams.yaml_frontmatter` | — | `agentteams.frameworks.agents_md`, `agentteams.frameworks.base`, `agentteams.frameworks.claude`, `agentteams.frameworks.copilot_vscode`, `agentteams.frameworks.goose`, `agentteams.interop` |

---

## External Dependencies

`build_team`, `jsonschema`

---

## DOT Source

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
    "agentteams" -> "agentteams.cli";
    "agentteams" -> "agentteams.frameworks";
    "agentteams.cli" -> "agentteams";
    "agentteams.cli" -> "agentteams.frameworks";
    "agentteams.frameworks" -> "agentteams";
}
```

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
      "external": []
    },
    "agentteams._utils": {
      "package": "agentteams",
      "path": "agentteams/_utils.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.advisory": {
      "package": "agentteams",
      "path": "agentteams/advisory.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.ai_bad_habits": {
      "package": "agentteams",
      "path": "agentteams/ai_bad_habits.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.analyze": {
      "package": "agentteams",
      "path": "agentteams/analyze.py",
      "is_package": false,
      "imports_internal": [
        "agentteams._utils",
        "agentteams.manifest_format",
        "agentteams.mcp_detect",
        "agentteams.output_plan",
        "agentteams.recipe_fields"
      ],
      "external": []
    },
    "agentteams.architecture": {
      "package": "agentteams",
      "path": "agentteams/architecture.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.atomicio": {
      "package": "agentteams",
      "path": "agentteams/atomicio.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.audit": {
      "package": "agentteams",
      "path": "agentteams/audit.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
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
      "external": []
    },
    "agentteams.baseline": {
      "package": "agentteams",
      "path": "agentteams/baseline.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.behavioral_drift": {
      "package": "agentteams",
      "path": "agentteams/behavioral_drift.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.handoff_payloads"
      ],
      "external": []
    },
    "agentteams.bridge": {
      "package": "agentteams",
      "path": "agentteams/bridge.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.backup",
        "agentteams.bridge_sources",
        "agentteams.bridge_subagents",
        "agentteams.bridge_subagents_goose",
        "agentteams.frameworks.goose",
        "agentteams.hooks_emit",
        "agentteams.instructions_split",
        "agentteams.interop",
        "agentteams.parallel_plan",
        "agentteams.plan_steps_todo",
        "agentteams.schedule_emit"
      ],
      "external": []
    },
    "agentteams.bridge_sources": {
      "package": "agentteams",
      "path": "agentteams/bridge_sources.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.bridge_subagents": {
      "package": "agentteams",
      "path": "agentteams/bridge_subagents.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.bridge_subagents_goose": {
      "package": "agentteams",
      "path": "agentteams/bridge_subagents_goose.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.bridge_subagents",
        "agentteams.frameworks.goose"
      ],
      "external": []
    },
    "agentteams.budget": {
      "package": "agentteams",
      "path": "agentteams/budget.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.cli": {
      "package": "agentteams",
      "path": "agentteams/cli/__init__.py",
      "is_package": true,
      "imports_internal": [],
      "external": []
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
        "agentteams.cli.parser",
        "agentteams.cli.recipe_check",
        "agentteams.cli.render_pipeline",
        "agentteams.fence_inject",
        "agentteams.fleet",
        "agentteams.frameworks.goose",
        "agentteams.git_hooks",
        "agentteams.host_features"
      ],
      "external": [
        "build_team"
      ]
    },
    "agentteams.cli.artifacts": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/artifacts.py",
      "is_package": false,
      "imports_internal": [
        "agentteams",
        "agentteams.drift",
        "agentteams.errors",
        "agentteams.eval_suite",
        "agentteams.mcp_emit",
        "agentteams.memory_index",
        "agentteams.memory_index_incremental",
        "agentteams.model_routing"
      ],
      "external": [
        "jsonschema"
      ]
    },
    "agentteams.cli.commands": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/commands.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.bridge",
        "agentteams.cli.security_gate",
        "agentteams.convert",
        "agentteams.drift",
        "agentteams.emit",
        "agentteams.frameworks.registry",
        "agentteams.interop",
        "agentteams.security_refs",
        "agentteams.stale_detector",
        "agentteams.stale_remediate"
      ],
      "external": []
    },
    "agentteams.cli.generate": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/generate.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.ai_bad_habits",
        "agentteams.analyze",
        "agentteams.audit",
        "agentteams.budget",
        "agentteams.cli.artifacts",
        "agentteams.cli.render_pipeline",
        "agentteams.cli.security_gate",
        "agentteams.drift",
        "agentteams.emit",
        "agentteams.enrich",
        "agentteams.errors",
        "agentteams.framework_research",
        "agentteams.frameworks.registry",
        "agentteams.git_hooks",
        "agentteams.graph",
        "agentteams.ingest",
        "agentteams.liaison_logs",
        "agentteams.render",
        "agentteams.scan",
        "agentteams.security_refs"
      ],
      "external": [
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
      "external": []
    },
    "agentteams.cli.parser": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/parser.py",
      "is_package": false,
      "imports_internal": [
        "agentteams",
        "agentteams.cli.goose_switch",
        "agentteams.cli.parser_validate",
        "agentteams.emit",
        "agentteams.frameworks.registry"
      ],
      "external": []
    },
    "agentteams.cli.parser_validate": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/parser_validate.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.cli.recipe_check": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/recipe_check.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.goose"
      ],
      "external": []
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
      "external": []
    },
    "agentteams.cli.security_gate": {
      "package": "agentteams.cli",
      "path": "agentteams/cli/security_gate.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.convert": {
      "package": "agentteams",
      "path": "agentteams/convert.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base",
        "agentteams.frameworks.registry"
      ],
      "external": []
    },
    "agentteams.drift": {
      "package": "agentteams",
      "path": "agentteams/drift.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.emit"
      ],
      "external": []
    },
    "agentteams.emit": {
      "package": "agentteams",
      "path": "agentteams/emit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.backup",
        "agentteams.fence_inject",
        "agentteams.fences"
      ],
      "external": []
    },
    "agentteams.enrich": {
      "package": "agentteams",
      "path": "agentteams/enrich/__init__.py",
      "is_package": true,
      "imports_internal": [
        "agentteams"
      ],
      "external": []
    },
    "agentteams.enrich._audit": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_audit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.enrich._fills",
        "agentteams.enrich._models",
        "agentteams.enrich._tools"
      ],
      "external": []
    },
    "agentteams.enrich._enrich": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_enrich.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.enrich._fills",
        "agentteams.enrich._models",
        "agentteams.enrich._notebooks",
        "agentteams.enrich._tools"
      ],
      "external": []
    },
    "agentteams.enrich._fills": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_fills.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.enrich._models": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_models.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.enrich._notebooks": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_notebooks.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.enrich._models",
        "agentteams.enrich._tools"
      ],
      "external": []
    },
    "agentteams.enrich._tools": {
      "package": "agentteams.enrich",
      "path": "agentteams/enrich/_tools.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.errors": {
      "package": "agentteams",
      "path": "agentteams/errors.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.eval_adapters": {
      "package": "agentteams",
      "path": "agentteams/eval_adapters/__init__.py",
      "is_package": true,
      "imports_internal": [],
      "external": []
    },
    "agentteams.eval_adapters.inspect_ai": {
      "package": "agentteams.eval_adapters",
      "path": "agentteams/eval_adapters/inspect_ai.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.eval_adapters.openai_evals": {
      "package": "agentteams.eval_adapters",
      "path": "agentteams/eval_adapters/openai_evals.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.eval_suite": {
      "package": "agentteams",
      "path": "agentteams/eval_suite.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.fence_inject": {
      "package": "agentteams",
      "path": "agentteams/fence_inject.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio",
        "agentteams.emit"
      ],
      "external": []
    },
    "agentteams.fences": {
      "package": "agentteams",
      "path": "agentteams/fences.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": []
    },
    "agentteams.fleet": {
      "package": "agentteams",
      "path": "agentteams/fleet.py",
      "is_package": false,
      "imports_internal": [],
      "external": [
        "build_team"
      ]
    },
    "agentteams.framework_research": {
      "package": "agentteams",
      "path": "agentteams/framework_research.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.frameworks": {
      "package": "agentteams",
      "path": "agentteams/frameworks/__init__.py",
      "is_package": true,
      "imports_internal": [],
      "external": []
    },
    "agentteams.frameworks.agents_md": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/agents_md.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base",
        "agentteams.yaml_frontmatter"
      ],
      "external": []
    },
    "agentteams.frameworks.base": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/base.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.yaml_frontmatter"
      ],
      "external": []
    },
    "agentteams.frameworks.claude": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/claude.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base",
        "agentteams.yaml_frontmatter"
      ],
      "external": []
    },
    "agentteams.frameworks.copilot_cli": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/copilot_cli.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base"
      ],
      "external": []
    },
    "agentteams.frameworks.copilot_vscode": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/copilot_vscode.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base",
        "agentteams.yaml_frontmatter"
      ],
      "external": []
    },
    "agentteams.frameworks.goose": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/goose.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.base",
        "agentteams.yaml_frontmatter"
      ],
      "external": []
    },
    "agentteams.frameworks.registry": {
      "package": "agentteams.frameworks",
      "path": "agentteams/frameworks/registry.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.agents_md",
        "agentteams.frameworks.base",
        "agentteams.frameworks.claude",
        "agentteams.frameworks.copilot_cli",
        "agentteams.frameworks.copilot_vscode",
        "agentteams.frameworks.goose"
      ],
      "external": []
    },
    "agentteams.git_hooks": {
      "package": "agentteams",
      "path": "agentteams/git_hooks.py",
      "is_package": false,
      "imports_internal": [
        "agentteams",
        "agentteams.architecture",
        "agentteams.emit",
        "agentteams.graph"
      ],
      "external": []
    },
    "agentteams.goose_config": {
      "package": "agentteams",
      "path": "agentteams/goose_config.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.graph": {
      "package": "agentteams",
      "path": "agentteams/graph.py",
      "is_package": false,
      "imports_internal": [
        "agentteams._utils"
      ],
      "external": []
    },
    "agentteams.handoff_payloads": {
      "package": "agentteams",
      "path": "agentteams/handoff_payloads.py",
      "is_package": false,
      "imports_internal": [],
      "external": [
        "jsonschema"
      ]
    },
    "agentteams.hooks_emit": {
      "package": "agentteams",
      "path": "agentteams/hooks_emit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": []
    },
    "agentteams.host_features": {
      "package": "agentteams",
      "path": "agentteams/host_features.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.ingest": {
      "package": "agentteams",
      "path": "agentteams/ingest.py",
      "is_package": false,
      "imports_internal": [
        "agentteams._utils"
      ],
      "external": []
    },
    "agentteams.instructions_split": {
      "package": "agentteams",
      "path": "agentteams/instructions_split.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.interop": {
      "package": "agentteams",
      "path": "agentteams/interop.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.frameworks.registry",
        "agentteams.yaml_frontmatter"
      ],
      "external": []
    },
    "agentteams.liaison_logs": {
      "package": "agentteams",
      "path": "agentteams/liaison_logs.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.man": {
      "package": "agentteams",
      "path": "agentteams/man.py",
      "is_package": false,
      "imports_internal": [],
      "external": [
        "build_team"
      ]
    },
    "agentteams.manifest_format": {
      "package": "agentteams",
      "path": "agentteams/manifest_format.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.mcp_detect": {
      "package": "agentteams",
      "path": "agentteams/mcp_detect.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
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
      ]
    },
    "agentteams.memory_index": {
      "package": "agentteams",
      "path": "agentteams/memory_index.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.memory_index_incremental": {
      "package": "agentteams",
      "path": "agentteams/memory_index_incremental.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.memory_index"
      ],
      "external": []
    },
    "agentteams.model_routing": {
      "package": "agentteams",
      "path": "agentteams/model_routing.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.output_plan": {
      "package": "agentteams",
      "path": "agentteams/output_plan.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.analyze"
      ],
      "external": []
    },
    "agentteams.parallel_plan": {
      "package": "agentteams",
      "path": "agentteams/parallel_plan.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.plan_steps": {
      "package": "agentteams",
      "path": "agentteams/plan_steps.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.plan_steps_todo": {
      "package": "agentteams",
      "path": "agentteams/plan_steps_todo.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.pr_management": {
      "package": "agentteams",
      "path": "agentteams/pr_management.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.recipe_fields": {
      "package": "agentteams",
      "path": "agentteams/recipe_fields.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.remediate": {
      "package": "agentteams",
      "path": "agentteams/remediate.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.render": {
      "package": "agentteams",
      "path": "agentteams/render.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.scan": {
      "package": "agentteams",
      "path": "agentteams/scan.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.schedule_emit": {
      "package": "agentteams",
      "path": "agentteams/schedule_emit.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.atomicio"
      ],
      "external": []
    },
    "agentteams.security_refs": {
      "package": "agentteams",
      "path": "agentteams/security_refs.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.stale_detector": {
      "package": "agentteams",
      "path": "agentteams/stale_detector.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.bridge",
        "agentteams.drift",
        "agentteams.fleet"
      ],
      "external": []
    },
    "agentteams.stale_remediate": {
      "package": "agentteams",
      "path": "agentteams/stale_remediate.py",
      "is_package": false,
      "imports_internal": [
        "agentteams.cli.commands",
        "agentteams.fleet",
        "agentteams.stale_detector"
      ],
      "external": []
    },
    "agentteams.vscode_tasks": {
      "package": "agentteams",
      "path": "agentteams/vscode_tasks.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    },
    "agentteams.yaml_frontmatter": {
      "package": "agentteams",
      "path": "agentteams/yaml_frontmatter.py",
      "is_package": false,
      "imports_internal": [],
      "external": []
    }
  },
  "package_edges": [
    {
      "source": "agentteams",
      "target": "agentteams.cli"
    },
    {
      "source": "agentteams",
      "target": "agentteams.frameworks"
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
      "source": "agentteams.frameworks",
      "target": "agentteams"
    }
  ],
  "module_edges": [
    {
      "source": "agentteams.analyze",
      "target": "agentteams._utils"
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
      "target": "agentteams.output_plan"
    },
    {
      "source": "agentteams.analyze",
      "target": "agentteams.recipe_fields"
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
      "source": "agentteams.bridge_subagents_goose",
      "target": "agentteams.bridge_subagents"
    },
    {
      "source": "agentteams.bridge_subagents_goose",
      "target": "agentteams.frameworks.goose"
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
      "source": "agentteams.cli.commands",
      "target": "agentteams.bridge"
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
      "target": "agentteams.interop"
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
      "target": "agentteams.budget"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.cli.artifacts"
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
      "target": "agentteams.liaison_logs"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.render"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.scan"
    },
    {
      "source": "agentteams.cli.generate",
      "target": "agentteams.security_refs"
    },
    {
      "source": "agentteams.cli.goose_switch",
      "target": "agentteams.goose_config"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams.cli.goose_switch"
    },
    {
      "source": "agentteams.cli.parser",
      "target": "agentteams.cli.parser_validate"
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
      "target": "agentteams.fence_inject"
    },
    {
      "source": "agentteams.emit",
      "target": "agentteams.fences"
    },
    {
      "source": "agentteams.enrich",
      "target": "agentteams"
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
      "source": "agentteams.fence_inject",
      "target": "agentteams.atomicio"
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
      "source": "agentteams.frameworks.copilot_cli",
      "target": "agentteams.frameworks.base"
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
      "target": "agentteams.frameworks.base"
    },
    {
      "source": "agentteams.frameworks.goose",
      "target": "agentteams.yaml_frontmatter"
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
      "source": "agentteams.git_hooks",
      "target": "agentteams"
    },
    {
      "source": "agentteams.git_hooks",
      "target": "agentteams.architecture"
    },
    {
      "source": "agentteams.git_hooks",
      "target": "agentteams.emit"
    },
    {
      "source": "agentteams.git_hooks",
      "target": "agentteams.graph"
    },
    {
      "source": "agentteams.graph",
      "target": "agentteams._utils"
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
      "target": "agentteams.frameworks.registry"
    },
    {
      "source": "agentteams.interop",
      "target": "agentteams.yaml_frontmatter"
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
      "source": "agentteams.output_plan",
      "target": "agentteams.analyze"
    },
    {
      "source": "agentteams.schedule_emit",
      "target": "agentteams.atomicio"
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
      "target": "agentteams.cli.commands"
    },
    {
      "source": "agentteams.stale_remediate",
      "target": "agentteams.fleet"
    },
    {
      "source": "agentteams.stale_remediate",
      "target": "agentteams.stale_detector"
    }
  ],
  "external_dependencies": [
    "build_team",
    "jsonschema"
  ]
}
```
<!-- AGENTTEAMS:END content -->
