# Claude Agent Infrastructure Expert Reference

Purpose: Canonical guidance for integrating Anthropic Claude Code sub-agent infrastructure into AgentTeamsModule.

## Authoritative Documentation

- Claude Code sub-agents:
  - https://docs.anthropic.com/en/docs/claude-code/sub-agents

## Canonical Output Conventions

- Claude sub-agent files:
  - Location: .claude/agents/
  - Extension: .md
  - Structure: Claude-compatible YAML front matter + Markdown body
  - Front matter keys used here: name, description, allowed-tools
- Claude project instructions:
  - Filename: CLAUDE.md
  - Location: repository root

## Function-Level Conformance Requirements

- Adapter layer must convert VS Code-oriented templates into Claude-compatible files:
  - Strip VS Code front matter keys and handoff sections
  - Inject Claude front matter with allowed-tools
- Pipeline must finalize output file naming for Claude architecture:
  - .agent.md -> .md for agent and builder outputs
  - ../copilot-instructions.md -> ../CLAUDE.md
- Emission and tests must assert Claude-native naming and structure.

## Integration Checklist

1. Extend adapter API to finalize paths by file type.
2. Ensure build pipeline calls path finalization for every rendered output.
3. Keep reference and artifact files at existing relative locations unless framework semantics require rename.
4. Add/maintain tests for CLAUDE.md naming and content transformation.
