# Agent Teams Module

Generate a complete, coordinated AI agent team for any project — from a single project description file.

---

## What It Does

Given a project description (a `.json` or `.md` brief), the module:

1. **Analyzes** the project goal, deliverables, tools, and components
2. **Selects** the right agent archetypes from a 4-tier taxonomy
3. **Renders** all agent files by filling in project-specific placeholders
4. **Emits** ready-to-use agent files for VS Code Copilot, Copilot CLI, or Claude

The generated team includes:

- 1 **Orchestrator** agent — coordinates all workflows
- 9 **Governance agents** — navigation, security, consistency, cleanup, documentation
- 2–9 **Domain agents** — appropriate archetypes for your deliverable type
- 1 **Workstream Expert** per project component — deep, component-specific knowledge
- 1 **Team Builder agent** — framework-native agent that can regenerate or expand the team
- `copilot-instructions.md` — project conventions and routing rules

---

## Workflow

![Adaptive Plan Execution Workflow](assets/images/workflow-12-adaptive-plan-execution.svg)

---

## Framework Support

| Framework | Format | Handoffs | Builder Agent |
|-----------|--------|----------|---------------|
| `copilot-vscode` | `.agent.md` with YAML front matter | ✅ | VS Code Copilot `.agent.md` |
| `copilot-cli` | Plain `.md` system prompts | ❌ | CLI prompt `.md` |
| `claude` | Plain `.md` | ❌ | `CLAUDE.md` system prompt |

---

## Install

AgentTeams is not yet published to PyPI. Install directly from the GitHub repository:

```bash
pip install git+https://github.com/jlcatonjr/agentteams.git
```

Or clone and install in editable mode for development:

```bash
git clone https://github.com/jlcatonjr/agentteams
cd agentteams
pip install -e .
```

Requires Python 3.11+. No external runtime dependencies (stdlib only).

---

## Quick Example

```bash
agentteams --description brief.json --project /path/to/project --framework copilot-vscode
```

See [Getting Started](getting-started.md) for a full walkthrough, [Agent-Assisted Setup](agent-assisted-setup.md) to use a coding agent to create your team interactively, or [CLI Reference](cli-reference.md) for all flags.
