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

### `scan_directory(agents_dir, *, expected_agent_names=None)`

> *Source: `agentteams/scan.py`*

Scan all `.agent.md`, `.md`, and `.json` files in `agents_dir` for
security issues.

**Args:**

- `agents_dir` (`Path`) — Path to the `.github/agents/` directory.
- `expected_agent_names` (`set[str] | None`, keyword-only) — *(T3a.2 v4)*
  When provided, `.agent.md` files whose basename is NOT in this
  set are treated as orphans from a prior team configuration and
  skipped. The build_team orphan advisory at `build_team.py:1304`
  surfaces them separately so they remain visible; double-flagging
  them in the security scan only blocks the daily pipeline without
  adding actionable signal. Default: `None` (scan every
  `.agent.md` found).

**Walk semantics:**

- The `.agentteams-backups/` subtree is always skipped.
  Point-in-time snapshots faithfully preserve historical
  (already-surfaced) content and should not gate the live scan.
- `_OPERATIONAL_JSON_NAMES` (`build-log.json`,
  `delivery-receipt.json`, `memory-index.json`, `eval-suite.json`,
  `doc-hashes.json`) suppresses the absolute-path PII detector,
  the entropy-based detectors, and unresolved-placeholder
  detection in those files. These are pipeline-controlled
  artefacts that legitimately carry paths, content hashes, and
  indexed copies of documentation. Pattern-based credential
  detection (`sk_live_*`, `xoxb-*`, etc.) still applies.
- Placeholder matches that fall entirely inside an inline-code
  span (`` `…` ``) on the same line are skipped — those are
  documentation prose mentioning placeholder names, not real
  unresolved placeholders.
- `_SECRET_CONTEXT_RE` is word-bounded so prose like "tokenized"
  or "authorize" does not elevate the line into secret-context
  scanning.

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
