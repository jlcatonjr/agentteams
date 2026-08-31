# Retrieval Trigger Contract

Project: {PROJECT_NAME}

Version: {RETRIEVAL_TRIGGER_CONTRACT_VERSION}

## Allowed Trigger Sources

{RETRIEVAL_TRIGGER_SOURCES}

## Requirements

1. Every maintenance entrypoint must map to at least one trigger source.
2. Every query entrypoint must identify a corresponding source-of-truth validation path.
3. Trigger changes must update this contract version and be reviewed by @adversarial and @conflict-auditor.

## Entrypoints

### Query

{RETRIEVAL_QUERY_ENTRYPOINTS}

### Maintenance

{RETRIEVAL_MAINTENANCE_ENTRYPOINTS}
