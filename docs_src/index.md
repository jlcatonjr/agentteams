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
- A framework instructions file — `.github/copilot-instructions.md` (Copilot VS Code / Copilot CLI) or `.claude/CLAUDE.md` (Claude)

---

## Workflow

![Adaptive Plan Execution Workflow](assets/images/workflow-12-adaptive-plan-execution.svg)

---

## Blog Series: AgentTeams Module Roles and Workflows

Short reads (about five paragraphs each) explaining how core module components fit together:

1. [Introduction](https://jameslcaton.com/#/blog/04-22-2026-AgentTeams%20Introduction.html)
2. [Adaptive Workflows with Step-by-Step Auditing and Revision](https://jameslcaton.com/#/blog/04-22-2026-AgentTeams%20Adaptive%20Workflows%20with%20Step-by-Step%20Auditing%20and%20Revision.html)
3. [Team Builder and Workstream Expert Agents](https://jameslcaton.com/#/blog/04-22-2026-AgentTeams%20Team%20Builder%20and%20Workstream%20Expert%20Agents.html)
4. [Orchestrator Agent](https://jameslcaton.com/#/blog/04-22-2026-AgentTeams%20Orchestrator%20Agent.html)
5. [Functional Agents](https://jameslcaton.com/#/blog/04-22-2026-AgentTeams%20Functional%20Agents.html)
6. [Domain Agents](https://jameslcaton.com/#/blog/04-22-2026-AgentTeams%20Domain%20Agents.html)
7. [Tools and References](https://jameslcaton.com/#/blog/04-22-2026-AgentTeams%20Tools%20and%20References.html)
8. [Security Agent](https://jameslcaton.com/#/blog/04-22-2026-AgentTeams%20Security%20Agent.html)
9. [Audit Protocols and Security](https://jameslcaton.com/#/blog/04-22-2026-AgentTeams%20Audit%20Protocols%20and%20Security.html)

---

## Framework Support

| Framework | Format | Handoffs | Builder Agent |
|-----------|--------|----------|---------------|
| `copilot-vscode` | `.agent.md` with YAML front matter | Native inline YAML | VS Code Copilot `.agent.md` |
| `copilot-cli` | Plain `.md` system prompts | Runtime manifest when handoffs are present (`references/runtime-handoffs.json`) | CLI prompt `.md` |
| `claude` | Claude front matter `.md` + `CLAUDE.md` instructions | Runtime manifest when handoffs are present (`references/runtime-handoffs.json`) | `CLAUDE.md` system prompt |

For `copilot-cli` and `claude`, AgentTeams strips inline handoff sections from the visible prompt files but emits `references/runtime-handoffs.json` when handoffs are extracted, so routing metadata remains available to bridge layers and other runtime tooling.

Default framework locations:
- `copilot-vscode`: `.github/agents/`
- `copilot-cli`: `.github/copilot/`
- `claude`: `.claude/agents/`

Framework instructions locations:
- `copilot-vscode`: `.github/copilot-instructions.md`
- `copilot-cli`: `.github/copilot-instructions.md`
- `claude`: `.claude/CLAUDE.md`

Three build paths:
- Path A: fresh generation from a brief (`--description`)
- Path B: format migration of an existing team (`--convert-from`)
- Path C: lightweight interface bridge to source canonical agents (`--bridge-from`)

---

## Interoperability Feature Family

Interoperability is now a first-class capability in AgentTeams with three explicit modes:

1. **Format migration** (`--convert-from`) for wrapper/front matter translation while preserving body prose.
2. **CAI interop pipeline** (`--interop-from`) for canonical normalization and optional compatibility bundles.
3. **Lightweight bridge** (`--bridge-from`) for source-canonical runtime integration without regenerating all source docs.

Open the dedicated [Interoperability](interoperability.md) tab for mode-by-mode guidance and API links.

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
