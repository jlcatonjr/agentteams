# `scan` — AgentTeamsModule

Proactive security scanner for generated agent files.

Scans `.agent.md` and related files for: absolute paths containing usernames (PII exposure), credential patterns (API keys, tokens, passwords), unresolved auto-placeholders (`{UPPER_SNAKE_CASE}`), and unresolved manual placeholders (`{MANUAL:*}`).

> *Source: `agentteams/scan.py`*

---

## Classes

### `ScanFinding`

> *Source: `agentteams/scan.py`*

A single security finding.

**Attributes:**

- `file` (`str`) — Relative path of the file containing the finding.
- `line` (`int`) — Line number (1-based).
- `category` (`str`) — Finding category (e.g., `'PII path'`, `'API key'`, `'Unresolved placeholder'`).
- `severity` (`str`) — `'high'`, `'medium'`, or `'low'`.
- `message` (`str`) — Human-readable description of the finding.
- `snippet` (`str`) — The offending line content (truncated).

---

### `ScanReport`

> *Source: `agentteams/scan.py`*

Results of a security scan.

**Attributes:**

- `scanned_files` (`int`) — Total number of files scanned.
- `findings` (`list[ScanFinding]`) — All findings across all files.

**Properties:**

- `has_issues` (`bool`) — `True` if any findings exist.
- `high_count` (`int`) — Count of high-severity findings.
- `medium_count` (`int`) — Count of medium-severity findings.
- `low_count` (`int`) — Count of low-severity findings.

---

## Functions

### `scan_directory(agents_dir)`

> *Source: `agentteams/scan.py`*

Scan all `.agent.md` and `.md` files in `agents_dir` for security issues.

**Args:**

- `agents_dir` (`Path`) — Path to the `.github/agents/` directory.

**Returns:** `ScanReport`

---

### `scan_content(content, filename='<string>')`

> *Source: `agentteams/scan.py`*

Scan a string of content for security issues.

**Args:**

- `content` (`str`) — File content to scan.
- `filename` (`str`) — Filename label for findings. Default: `'<string>'`.

**Returns:** `list[ScanFinding]`

---

### `print_scan_report(report)`

> *Source: `agentteams/scan.py`*

Print a human-readable scan report to stdout.

**Args:**

- `report` (`ScanReport`) — Result from `scan_directory()`.
