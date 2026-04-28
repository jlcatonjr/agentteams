# API Reference — AgentTeamsModule

Public API for the `agentteams` package. Each module corresponds to a stage in the pipeline or a support capability.

This reference defines the **supported public API contract**. Modules and symbols not documented here are considered internal and may change without notice.

---

## Pipeline Modules

| Module | Role |
|--------|------|
| [`ingest`](ingest.md) | Load and normalize project description files |
| [`analyze`](analyze.md) | Classify project type, select archetypes, build team manifest |
| [`render`](render.md) | Resolve templates and produce rendered agent file content |
| [`emit`](emit.md) | Write rendered agent files to disk safely |

## Support Modules

| Module | Role |
|--------|------|
| [`convert`](convert.md) | Direct format migration between framework outputs |
| [`interop`](interop.md) | Canonical Agent Interface (CAI) interop pipeline |
| [`bridge`](bridge.md) | Lightweight runtime compatibility bridge artifacts |
| [`drift`](drift.md) | Detect template-to-instance drift for incremental updates |
| [`scan`](scan.md) | Proactive security scan for generated agent files |
| [`audit`](audit.md) | Post-generation static and AI-powered audit |
| [`remediate`](remediate.md) | Auto-correct audit findings via standalone Copilot CLI |
| [`enrich`](enrich.md) | Default-value audit and context-aware placeholder enrichment |
| [`graph`](graph.md) | Directed graph inference for agent team topology |
| [`frameworks`](frameworks.md) | Per-framework adapter classes |
| [`man`](man.md) | Generate and validate the project man-page source |

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
