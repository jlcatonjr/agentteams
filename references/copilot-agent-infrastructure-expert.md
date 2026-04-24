# Copilot Agent Infrastructure Expert Reference

Purpose: Canonical guidance for integrating GitHub Copilot agent infrastructure into AgentTeamsModule.

## Authoritative Documentation

- VS Code custom chat modes and agents:
  - https://code.visualstudio.com/docs/copilot/customization/custom-chat-modes
  - https://code.visualstudio.com/docs/copilot/customization/custom-instructions
- GitHub Copilot CLI:
  - https://docs.github.com/en/copilot/github-copilot-in-the-cli/about-github-copilot-in-the-cli

## Canonical Output Conventions

- VS Code Copilot agent files:
  - Location: .github/agents/
  - Extension: .agent.md
  - Structure: YAML front matter + Markdown body
- Copilot CLI prompt files:
  - Location: .github/copilot/
  - Extension: .md
  - Structure: plain Markdown prompt body (no VS Code metadata front matter)
- Project instructions:
  - Filename: copilot-instructions.md
  - Location: repository root

## Function-Level Conformance Requirements

- Adapter layer must enforce format-specific transformations:
  - VS Code: ensure required YAML keys exist and are normalized
  - CLI: strip VS Code-only YAML keys and handoff sections
- Pipeline must finalize output paths using framework adapter rules so that file names and extensions are framework-native.
- Emission and tests must validate behavior across both Copilot targets (VS Code and CLI).

## Integration Checklist

1. Ensure framework adapter API covers file content and output path finalization.
2. Apply adapter-driven path finalization in pipeline before emit.
3. Keep instructions filename stable as copilot-instructions.md for Copilot frameworks.
4. Add/maintain tests for extension + content transformation parity.
