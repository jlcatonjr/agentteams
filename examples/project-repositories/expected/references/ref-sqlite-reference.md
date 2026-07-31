<!--
SECTION MANIFEST
| section_id       | designation  |
|------------------|--------------|
| tool_api_surface | FENCED       |
| key_api_surface  | FENCED       |
| schema_management| FENCED       |
| query_standards  | FENCED       |
| backup_recovery  | FENCED       |
| when_to_involve_the_team| FENCED       |
-->

# SQLite — Database Reference — ProjectRepositories

> Operational reference for the **SQLite ** database in ProjectRepositories.
> SQLite is infrastructure the team *uses* — not an agent. Schema, migration, and
> credential changes should follow the procedures below; route credential or
> access-control changes through `@security`.

**Database:** `SQLite` ``
**Configuration files:** `N/A`

---

## Official Documentation

Consult the official SQLite documentation at: https://www.sqlite.org/docs.html

Verify SQL dialect features, configuration parameters, and data types against this documentation.

<!-- AGENTTEAMS:BEGIN key_api_surface v=1 -->
## Key API Surface
<!-- AGENTTEAMS:END key_api_surface -->

<!-- AGENTTEAMS:BEGIN tool_api_surface v=1 -->
sqlite3 CLI, CREATE TABLE, CREATE INDEX, PRAGMA, EXPLAIN QUERY PLAN
<!-- AGENTTEAMS:END tool_api_surface -->

<!-- Document the primary SQL dialect features, system tables, administrative commands, and driver-specific APIs for SQLite. -->

## Common Patterns & Pitfalls

Use parameterized queries, explicit transactions, and indexes validated with EXPLAIN QUERY PLAN.

<!-- Document common schema patterns, query optimization practices, and known issues for SQLite . -->

---

<!-- AGENTTEAMS:BEGIN schema_management v=1 -->
## Schema Management

1. All schema changes must be expressed as versioned migrations
2. Before applying a migration: verify it is backward-compatible with the current schema version
3. Never drop tables or columns without `@security` clearance
4. Document all schema changes in the migration file header
<!-- AGENTTEAMS:END schema_management -->

<!-- AGENTTEAMS:BEGIN query_standards v=1 -->
## Query Standards

1. All queries must use parameterized statements — **no string concatenation**
2. Verify query plans for any query touching > 10 000 rows
3. Index recommendations must cite the specific query they optimise
4. All queries must be tested against representative data volumes
<!-- AGENTTEAMS:END query_standards -->

## Config Management

Current configuration lives in: `N/A`

Before any configuration change:
1. Read the current configuration file
2. Verify the proposed change is compatible with ``
3. If the change modifies credentials or access controls, request clearance from `@security`
4. Back up the existing config before writing
5. Apply the change and verify connectivity

<!-- AGENTTEAMS:BEGIN backup_recovery v=1 -->
## Backup & Recovery

1. Verify backup schedule is documented
2. Never overwrite existing backups without confirmation
3. Test restore procedures against a non-production copy
<!-- AGENTTEAMS:END backup_recovery -->

<!-- AGENTTEAMS:BEGIN when_to_involve_the_team v=1 -->
## When to Involve the Team

Raise with `@orchestrator` (and `@security` for credentials) when:
- A migration fails or produces unexpected schema state
- Query performance degrades > 2× after a change
- Credential rotation is required
- Data loss or corruption is suspected
<!-- AGENTTEAMS:END when_to_involve_the_team -->

