# `scan` — AgentTeamsModule

Proactive security scanner for generated agent files.

Scans `.agent.md` and related files for: absolute paths containing usernames (PII exposure), credential patterns (API keys, tokens, passwords), unresolved auto-placeholders (`{UPPER_SNAKE_CASE}`), and unresolved manual placeholders (`{MANUAL:*}`).

`scan_content()` is content-only (no filesystem coupling), so it doubles as a review-time check: `security.template.md` Rules S-1 and S-8 cite it directly as the preferred way to verify a piece of reviewed content, with the existing manual-pattern bullets retained as a fallback for runtimes that can't execute Python.

## Layout

- **Module:** `agentteams.scan` (importable)
- **CLI:** `python -m agentteams.scan <path>` (or `-` for stdin) — for a runtime with shell/`execute` access but no way to natively `import` and call `scan_content` directly.

> *Source: `agentteams/scan.py`*

---

## Classes

### `ScanFinding`

> *Source: `agentteams/scan.py`*

A single security finding.

**Attributes:**

- `file` (`str`) — Relative path of the file containing the finding.
- `line` (`int`) — Line number (1-based).
- `category` (`str`) — Finding category (e.g., `'PII'`, `'credential'`, `'unresolved-placeholder'`).
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
- `verdict` (`str`) — `HALT` / `CONDITIONAL_PASS` / `PASS`, computed from `self.findings` via `verdict_for_findings()`.

---

## Constants

### `HALT`, `CONDITIONAL_PASS`, `PASS`

> *Source: `agentteams/scan.py`*

The three verdict strings `verdict_for_findings()` returns (each defined in the [Security Workflow Glossary](../security-workflow-glossary.md)). Mirror the `HALT` / `CONDITIONAL PASS` / `PASS` verdicts in `security.template.md`'s escalation table — specifically the Credential, PII-path, and Machine-specific-information rows, plus (since 2026-07-31) Rule S-5's literal instruction-override/identity-override patterns and C-1's tier-claim patterns, the scan-derivable subset of that table. `_check_injection()` runs on every scanned line against `_INJECTION_PATTERNS`, `_IDENTITY_OVERRIDE_PATTERNS`, and `_TIER_CLAIM_PATTERNS`, emitting `category="injection"`, `severity="high"` findings — which means a matched injection attempt is mechanized and HALT-triggering, not a judgment call left to the agent. What remains genuinely out of scan-derivable scope: destructive-op confirmation, external-repo writes, scope violations, and S-5's third bullet (a heading that redefines agent identity), which needs judgment rather than pattern matching.

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
  set are **not** skipped: they are scanned normally, like every
  other file, and additionally flagged as an `"orphan-agent"`
  finding at `medium` severity (non-blocking under
  `verdict_for_findings`, but no longer silent). Orphans were
  skipped entirely until 2026-08-06, which made an injected agent
  file the one class of agent file the scanner never read. Default:
  `None` — no orphan-flagging occurs, but scanning coverage is
  unaffected either way; every `.agent.md` found is always scanned
  regardless of this parameter.

**Walk semantics:**

- The `.agentteams-backups/` subtree is always skipped.
  Point-in-time snapshots faithfully preserve historical
  (already-surfaced) content and should not gate the live scan.
- `_OPERATIONAL_JSON_NAMES` (`build-log.json`,
  `delivery-receipt.json`, `memory-index.json`, `eval-suite.json`,
  `doc-hashes.json`, `security-vulnerability-watch.json`) suppresses the absolute-path PII detector,
  the entropy-based detectors, and unresolved-placeholder
  detection in those files. These are pipeline-controlled
  artefacts that legitimately carry paths, content hashes, and
  indexed copies of documentation. Pattern-based credential
  detection (`sk_live_*`, `xoxb-*`, etc.) still applies. The
  exemption additionally requires the file to reside directly in
  the agents dir's `references/` subdirectory (`_is_operational_json`)
  — a basename match alone is not enough. This is a deliberate
  anti-spoofing design point: keying the exemption on basename
  alone would let an agent with write access pre-blind the scanner
  to its own payload just by choosing a matching filename anywhere
  else in the tree.
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

---

### `verdict_for_findings(findings)`

> *Source: `agentteams/scan.py`*

Map scan findings to a `HALT` / `CONDITIONAL_PASS` / `PASS` verdict: any `high`-severity finding is `HALT`; any finding at all (with no `high`) is `CONDITIONAL_PASS`; no findings is `PASS`.

**Args:**

- `findings` (`Iterable[ScanFinding]`) — Findings to evaluate — typically `scan_content()`'s return value or a `ScanReport.findings` list.

**Returns:** `str` — One of `HALT`, `CONDITIONAL_PASS`, `PASS`.

## CLI

```bash
# Scan a file, print JSON findings + verdict, exit 1 iff verdict is HALT
python -m agentteams.scan path/to/file.md

# Scan piped content (e.g. a diff) via stdin
git diff | python -m agentteams.scan -
```

## See Also

- [`session_scan`](session_scan.md) — a sibling review-time utility (repo at-large issue scan) with its own `python -m` entrypoint.
- [`integrity`](integrity.md) — the hash manifest that lists `scan.py` among the enforcement modules it protects.
- [`audit`](audit.md) — the post-generation audit that runs alongside this proactive scan.
- [`redteam`](redteam.md) — the standing constitutional red-team battery this scanner feeds as an enforcement input.
