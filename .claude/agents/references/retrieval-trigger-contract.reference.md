<!-- AGENTTEAMS:BEGIN content v=1 -->
# Retrieval Trigger Contract

Project: AgentTeamsModule

Version: v1

## Allowed Trigger Sources

- cli
- env
- script
- workflow

## Requirements

1. Every maintenance entrypoint must map to at least one trigger source.
2. Every query entrypoint must identify a corresponding source-of-truth validation path.
3. Trigger changes must update this contract version and be reviewed by @adversarial and @conflict-auditor.

## Entrypoints

### Query

No retrieval query entrypoints declared.

### Maintenance

- scripts/run_daily_security_maintenance.sh
- scripts/research_ai_bad_habits.py
- scripts/research_claude_code_docs.py
- scripts/run_daily_bridge_maintenance.sh
- .github/workflows/framework-auto-update.yml
- .github/workflows/advisory-pr.yml
- CLAUDE.md
- README.md
- build_team.py
<!-- AGENTTEAMS:END content -->
