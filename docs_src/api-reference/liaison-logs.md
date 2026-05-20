# `liaison_logs` — AgentTeamsModule

Manage external CSV log files for cross-repository coordination and security decisions.

Provides utilities to initialize, migrate, and maintain machine-readable CSV logs that track:
- Per-repository changelog entries (file changes across adjacent repos)
- Cross-orchestrator coordination (inter-team handoff records)
- Security decisions (audit trail of `@security` verdicts)

> *Source: `agentteams/liaison_logs.py`*

---

## Constants

### CSV File Names and Headers

| Constant | Value | Purpose |
|----------|-------|---------|
| `CHANGELOG_CSV` | `adjacent-repos-changelog.csv` | Per-repo file change log |
| `COORD_LOG_CSV` | `adjacent-repos-coordination-log.csv` | Cross-orchestrator coordination records |
| `SECURITY_DECISIONS_CSV` | `security-decisions.log.csv` | Security decision audit trail |

### Column Headers

| CSV | Headers |
|-----|---------|
| **Changelog** | `date`, `repo_name`, `action`, `files_changed`, `summary` |
| **Coordination Log** | `date`, `adjacent_repo`, `direction`, `outcome` |
| **Security Decisions** | `timestamp`, `requesting_agent`, `action_reviewed`, `verdict`, `conditions`, `conditions_verified` |

---

## Classes

### `MigrateResult`

> *Source: `agentteams/liaison_logs.py`*

Result of a `migrate_inline_logs()` operation.

**Attributes:**

- `changelog_rows_moved` (`int`) — Number of changelog rows extracted from markdown to CSV
- `coord_log_rows_moved` (`int`) — Number of coordination log rows extracted to CSV
- `adjacent_repos_md_updated` (`bool`) — `True` if the markdown file was rewritten
- `skipped` (`bool`) — `True` if the markdown file did not exist
- `errors` (`list[str]`) — Error messages for any failures

**Properties:**

- `rows_moved` (`int`) — Total rows moved across all logs
- `success` (`bool`) — `True` if no errors occurred

---

## Functions

### `init_csv_stubs(refs_dir)`

> *Source: `agentteams/liaison_logs.py`*

Initialize CSV stub files (header row only) if they do not already exist.

**Args:**

- `refs_dir` (`Path`) — Path to the `references/` directory in `.github/agents/`

**Behavior:**

- Creates three CSV files: changelog, coordination log, security decisions
- Safe to call on every generation run (never overwrites existing data)
- Creates only the header row; no data rows

---

### `migrate_inline_logs(adjacent_repos_md, refs_dir)`

> *Source: `agentteams/liaison_logs.py`*

Migrate inline markdown log tables from `adjacent-repos.md` into external CSV files.

**Args:**

- `adjacent_repos_md` (`Path`) — Path to the `adjacent-repos.md` file
- `refs_dir` (`Path`) — Path to the `references/` directory

**Returns:** `MigrateResult` — Description of what was extracted and moved

**Behavior:**

- Scans markdown for inline table sections (changelog, coordination log)
- Extracts data rows to corresponding CSV files
- Rewrites the markdown to reference the CSVs instead of inline tables
- Idempotent: safe to call multiple times

**Note:** This utility is useful for one-time migration of existing inline logs to machine-readable CSV format.

---

## Typical Usage

```python
from pathlib import Path
from agentteams.liaison_logs import init_csv_stubs, migrate_inline_logs

refs_dir = Path(".github/agents/references")
adjacent_md = Path(".github/agents/references/adjacent-repos.md")

# Initialize CSV files on first generation
init_csv_stubs(refs_dir)

# Later: migrate any existing inline markdown logs to CSV
if adjacent_md.exists():
    result = migrate_inline_logs(adjacent_md, refs_dir)
    if result.success:
        print(f"✓ Migrated {result.rows_moved} rows to CSV")
    else:
        print(f"❌ Errors: {result.errors}")
```

---

## CSV Format

CSV files are stored in `references/` (not in markdown tables) to:
- Keep them machine-readable and parseable without regex extraction
- Prevent agent file bloat as logs accumulate
- Enable scripted analysis and auditing
- Support version control and diffing

Each row is idempotent (same timestamp, actor, and action key can be re-added without duplication issues).
