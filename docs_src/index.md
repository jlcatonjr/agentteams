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

## Blog Series: AgentTeams Module Roles and Workflows

Short reads (about five paragraphs each) explaining how core module components fit together:

1. [Introduction](https://jlcatonjr.github.io/InCognito/post.html?post=04-22-2026-AgentTeams%20Introduction.html)
2. [Adaptive Workflows with Step-by-Step Auditing and Revision](https://jlcatonjr.github.io/InCognito/post.html?post=04-22-2026-AgentTeams%20Adaptive%20Workflows%20with%20Step-by-Step%20Auditing%20and%20Revision.html)
3. [Team Builder and Workstream Expert Agents](https://jlcatonjr.github.io/InCognito/post.html?post=04-22-2026-AgentTeams%20Team%20Builder%20and%20Workstream%20Expert%20Agents.html)
4. [Orchestrator Agent](https://jlcatonjr.github.io/InCognito/post.html?post=04-22-2026-AgentTeams%20Orchestrator%20Agent.html)
5. [Functional Agents](https://jlcatonjr.github.io/InCognito/post.html?post=04-22-2026-AgentTeams%20Functional%20Agents.html)
6. [Domain Agents](https://jlcatonjr.github.io/InCognito/post.html?post=04-22-2026-AgentTeams%20Domain%20Agents.html)
7. [Tools and References](https://jlcatonjr.github.io/InCognito/post.html?post=04-22-2026-AgentTeams%20Tools%20and%20References.html)

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
