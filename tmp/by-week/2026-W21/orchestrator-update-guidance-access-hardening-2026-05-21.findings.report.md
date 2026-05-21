# Findings Report — Orchestrator Access to Agent-Update Guidance (2026-05-21)

## Scope

- Question: Do generated agent-team orchestrators have ready access to documents that guide creation/editing of agent files in an `agentteams`-compatible update model?
- Sources reviewed:
  - `agentteams/templates/universal/orchestrator.template.md`
  - `agentteams/templates/universal/agent-updater.template.md`
  - `agentteams/templates/copilot-instructions.template.md`
  - `agentteams/templates/PLACEHOLDER-CONVENTIONS.md`
  - `README.md` (update/drift sections)

## Findings

1. **Access exists, but is distributed across multiple files.**
   - Orchestrator guidance includes plan/audit governance, authority hierarchy, and explicit workflow references.
   - Agent-updater guidance contains detailed `--check`/`--update --merge` protocols and update compatibility maintenance practices.
   - Copilot instructions expose authority hierarchy and source repositories.

2. **Update-critical guidance is present but not centralized for the orchestrator at point-of-action.**
   - The orchestrator template references policy and routing, but does not provide a compact "open these files first" source pack for update compatibility execution.
   - In practice, operators must already know where update protocols and placeholder/fence conventions live.

3. **Current structure can lead to discoverability lag during on-the-fly updates.**
   - The normative content exists, but fast-path discoverability is uneven across generated teams.
   - This increases risk of partial protocol execution (for example, applying update commands without checking related conventions docs).

## Conclusion

- **Answer:** Yes, orchestrators currently have access to guiding documents, but access is not yet consistently "ready" in a single, high-salience section within the orchestrator file itself.
- **Gap type:** Discoverability and operational ergonomics, not missing core policy.

## Remediation Direction

1. Add a dedicated **Update Compatibility Source Pack** section to orchestrator template, listing the canonical files and command sequence for update-safe operations.
2. Keep this section in a fenced, module-owned region so future `--update --merge` can maintain it consistently.
3. Add a focused test assertion to ensure generated orchestrators continue to carry the source-pack cues.