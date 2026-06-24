"""
scan.py — Proactive security scanner for generated agent files.

Scans .agent.md and related files in a generated agents directory for:
  - Absolute paths containing usernames (PII exposure)
  - Credential patterns (API keys, tokens, passwords)
    - High-entropy secret-like tokens in sensitive contexts
  - Unresolved auto-placeholders ({UPPER_SNAKE_CASE} tokens)
  - Unresolved manual placeholders ({MANUAL:*} still present after setup)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

#: Absolute paths with username components (macOS, Linux, Windows).
#: The trailing path segment is optional so a *bare* home dir (e.g.
#: ``/Users/alice`` with no child path) is still flagged as username PII.
_PII_PATH_RE = re.compile(
    r"(/Users/[a-zA-Z][a-zA-Z0-9._-]+"
    r"|/home/[a-zA-Z][a-zA-Z0-9._-]+"
    r"|[A-Z]:\\Users\\[a-zA-Z][a-zA-Z0-9._-]+)"
)

#: Common credential patterns. Prefixed-token formats are matched explicitly
#: (the generic entropy detector below misses them when they sit on a line with
#: no secret-context keyword).
_CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("API key", re.compile(r"(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{20,}", re.IGNORECASE)),
    ("Bearer token", re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("GitHub fine-grained PAT", re.compile(r"github_pat_[A-Za-z0-9_]{60,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe secret key", re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}")),
    ("Private key header", re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----")),
    ("Password assignment", re.compile(r"(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{4,}", re.IGNORECASE)),
    ("Connection string", re.compile(r"(?:postgres|mysql|mongodb)://[^\s]+:[^\s]+@", re.IGNORECASE)),
]

#: High-entropy tokens that look like opaque secrets when they are contiguous
#: opaque strings rather than human-readable identifiers.
_HIGH_ENTROPY_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/=])"
    r"(?=[A-Za-z0-9+/=]{24,}(?![A-Za-z0-9+/=]))"
    r"(?=.*[A-Za-z])"
    r"(?=.*\d)"
    r"[A-Za-z0-9+/=]{24,}"
)

#: Wider token matcher used only when the line already looks like secret
#: material. This catches separator-rich tokens that would otherwise be missed.
_SENSITIVE_CONTEXT_TOKEN_RE = re.compile(r"(?<!\S)[A-Za-z0-9+/=_-]{20,}(?!\S)")

#: Sensitive context keywords that raise the severity of opaque token findings.
#: T3a.2 v3: word-bounded so prose like "tokenized", "authorize", "passport"
#: doesn't falsely flag adjacent identifier-shaped strings as secrets.
_SECRET_CONTEXT_RE = re.compile(
    r"\b(?:secret|token|credential|auth|password|passwd|bearer)\b|private\s+key",
    re.IGNORECASE,
)

#: Unresolved auto-placeholder tokens {UPPER_SNAKE_CASE}
_UNRESOLVED_AUTO_RE = re.compile(r"\{([A-Z][A-Z0-9_]{2,})\}")

#: Unresolved manual placeholder tokens {MANUAL:NAME}
_UNRESOLVED_MANUAL_RE = re.compile(r"\{MANUAL:([A-Z][A-Z0-9_]*)\}")

#: Known safe tokens that look like placeholders but aren't
_SAFE_TOKENS = frozenset({
    "TRUE", "FALSE", "NULL", "NONE", "TODO", "FIXME", "NOTE",
    "TBD", "REQUIRED", "OPTIONAL", "DEPRECATED",
    # T3a.2 v4: meta-documentation tokens that name the placeholder
    # convention itself. These appear in descriptors and reference
    # files as documentation, never as real unresolved placeholders.
    "PLACEHOLDER", "UPPER_SNAKE_CASE",
})


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScanFinding:
    """A single security finding."""
    file: str
    line: int
    category: str
    severity: str  # "high", "medium", "low"
    message: str
    snippet: str


@dataclass
class ScanReport:
    """Results of a security scan."""
    scanned_files: int = 0
    findings: list[ScanFinding] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        """Return True if any findings exist."""
        return len(self.findings) > 0

    @property
    def high_count(self) -> int:
        """Return the count of high-severity findings."""
        return sum(1 for f in self.findings if f.severity == "high")

    @property
    def medium_count(self) -> int:
        """Return the count of medium-severity findings."""
        return sum(1 for f in self.findings if f.severity == "medium")

    @property
    def low_count(self) -> int:
        """Return the count of low-severity findings."""
        return sum(1 for f in self.findings if f.severity == "low")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def scan_directory(
    agents_dir: Path,
    *,
    expected_agent_names: set[str] | None = None,
) -> ScanReport:
    """Scan all agent files in a directory for security issues.

    Args:
        agents_dir: Path to the .github/agents/ directory.
        expected_agent_names: When provided, `.agent.md` files whose basename
            is NOT in this set are treated as orphans from a prior team
            configuration and skipped. The orphan advisory at
            build_team.py:1304 surfaces them separately so they remain
            visible; double-flagging them here only blocks the daily
            pipeline without adding actionable signal. (T3a.2 v4.)

    Returns:
        ScanReport with all findings.
    """
    report = ScanReport()

    # Scan .agent.md files and related files
    scan_patterns = ["*.agent.md", "*.md", "*.json"]
    files_to_scan: list[Path] = []
    for pattern in scan_patterns:
        files_to_scan.extend(agents_dir.rglob(pattern))

    # T3a.2: skip the merge engine's backup directory. Backups are
    # point-in-time snapshots; any flagged token inside them was already
    # surfaced when that content was live, so re-flagging them only
    # produces stale-looking false positives.
    # Plan: references/plans/T3a-2-scan-skip-backups-2026-05-25.plan.md
    files_to_scan = [
        p for p in files_to_scan
        if ".agentteams-backups" not in p.parts
    ]

    # T3a.2 v4: drop orphan .agent.md files when the caller provided the
    # current team's expected agent-name set.
    if expected_agent_names is not None:
        files_to_scan = [
            p for p in files_to_scan
            if not p.name.endswith(".agent.md") or p.name in expected_agent_names
        ]

    # Also scan copilot-instructions.md one level up
    instructions = agents_dir.parent / "copilot-instructions.md"
    if instructions.exists():
        files_to_scan.append(instructions)

    seen: set[Path] = set()
    for file_path in sorted(files_to_scan):
        resolved = file_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        _scan_file(file_path, agents_dir, report)

    report.scanned_files = len(seen)
    return report


def scan_content(content: str, filename: str = "<string>") -> list[ScanFinding]:
    """Scan a string of content for security issues.

    Args:
        content:  Text content to scan.
        filename: Filename for reporting purposes.

    Returns:
        List of ScanFinding objects.
    """
    findings: list[ScanFinding] = []
    for line_num, line in enumerate(content.splitlines(), start=1):
        _check_line(line, line_num, filename, findings)
    return findings


def print_scan_report(report: ScanReport) -> None:
    """Print a human-readable scan report to stdout.

    Args:
        report: ScanReport from scan_directory().
    """
    print(f"\nSecurity scan: {report.scanned_files} file(s) scanned")

    if not report.has_issues:
        print("  No issues found.")
        return

    print(f"  Findings: {report.high_count} high, {report.medium_count} medium, {report.low_count} low")
    if report.high_count == 0:
        print("  (medium/low findings are informational — only high-severity findings are blocking)")
    print()

    # Group by file
    by_file: dict[str, list[ScanFinding]] = {}
    for finding in report.findings:
        by_file.setdefault(finding.file, []).append(finding)

    for filepath, findings in sorted(by_file.items()):
        print(f"  {filepath}:")
        for f in findings:
            severity_marker = {"high": "!!!", "medium": "!!", "low": "!"}[f.severity]
            print(f"    L{f.line} [{severity_marker} {f.category}] {f.message}")
            if f.snippet:
                # Truncate long snippets
                snippet = f.snippet[:80] + "..." if len(f.snippet) > 80 else f.snippet
                print(f"         {snippet}")
        print()


# ---------------------------------------------------------------------------
# Internal scanning logic
# ---------------------------------------------------------------------------

# T3a.2 v2: operational-metadata JSON files written by the build
# pipeline legitimately carry absolute paths into the working tree
# (delivery receipts, memory index, build log, etc.). Suppress the
# absolute-path PII detector for these specific files. Other
# detectors (credentials, entropy) still apply.
_OPERATIONAL_JSON_NAMES = frozenset({
    "build-log.json",
    "delivery-receipt.json",
    "memory-index.json",
    "eval-suite.json",
    "doc-hashes.json",
})


def _match_inside_code_span(line: str, start: int, end: int) -> bool:
    """Return True when [start, end) on *line* lies fully inside a
    backtick-delimited inline-code span. Used to suppress
    documentation placeholders from unresolved-placeholder detection.
    """
    # Walk paired backticks left-to-right.
    i = 0
    in_span = False
    span_start = -1
    while i < len(line):
        if line[i] == "`":
            if not in_span:
                in_span = True
                span_start = i + 1
            else:
                if span_start <= start and end <= i:
                    return True
                in_span = False
        i += 1
    return False


def _scan_file(file_path: Path, agents_dir: Path, report: ScanReport) -> None:
    """Scan a single file and append findings to report."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    try:
        rel_path = str(file_path.relative_to(agents_dir.parent.parent))
    except ValueError:
        rel_path = str(file_path)

    is_operational_json = file_path.name in _OPERATIONAL_JSON_NAMES
    for line_num, line in enumerate(content.splitlines(), start=1):
        _check_line(
            line, line_num, rel_path, report.findings,
            skip_pii_path=is_operational_json,
            skip_entropy=is_operational_json,
            skip_placeholders=is_operational_json,
        )


def _check_line(
    line: str,
    line_num: int,
    filepath: str,
    findings: list[ScanFinding],
    *,
    skip_pii_path: bool = False,
    skip_entropy: bool = False,
    skip_placeholders: bool = False,
) -> None:
    """Check a single line for all security patterns."""
    # Skip markdown comments and code fence markers
    stripped = line.strip()
    if stripped.startswith("<!--") or stripped.startswith("```"):
        return

    # PII: absolute paths with usernames
    if not skip_pii_path:
        for match in _PII_PATH_RE.finditer(line):
            findings.append(ScanFinding(
                file=filepath,
                line=line_num,
                category="PII",
                severity="high",
                message=f"Absolute path with username: {match.group(0)}",
                snippet=stripped,
            ))

    # Credentials
    for cred_name, pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(line):
            findings.append(ScanFinding(
                file=filepath,
                line=line_num,
                category="credential",
                severity="high",
                message=f"Possible {cred_name} detected",
                snippet=stripped,
            ))

    # Entropy/secret-context detection is suppressed in operational JSON
    # files (memory-index.json etc.) where high-entropy SHA hashes and
    # identifier keys are operational metadata, not credentials. Pattern-
    # based credential checks above (sk_live_, xoxb-, etc.) still apply.
    if skip_entropy:
        token_regex = None
    else:
        secret_context = _SECRET_CONTEXT_RE.search(line) is not None
        token_regex = _SENSITIVE_CONTEXT_TOKEN_RE if secret_context else _HIGH_ENTROPY_TOKEN_RE
    for token_match in (token_regex.finditer(line) if token_regex else ()):
        token = token_match.group(0)
        entropy = _token_entropy(token)
        if entropy < 3.8:
            continue
        severity = "high" if secret_context or entropy >= 4.2 else "medium"
        findings.append(ScanFinding(
            file=filepath,
            line=line_num,
            category="credential",
            severity=severity,
            message=(
                "Possible secret-like token detected"
                if secret_context
                else "High-entropy token detected"
            ),
            snippet=stripped,
        ))

    # Unresolved auto-placeholders (excluding known safe tokens).
    # Suppressed in operational JSON where indexed copies of doc/template
    # content legitimately mention placeholder names; the source files
    # are scanned separately.
    if skip_placeholders:
        return
    for match in _UNRESOLVED_AUTO_RE.finditer(line):
        token = match.group(1)
        if token in _SAFE_TOKENS:
            continue
        # Skip if it's inside a {MANUAL:...} wrapper
        full_match = match.group(0)
        start = match.start()
        if start >= 7 and line[start - 7:start] == "MANUAL:":
            continue
        # Skip tokens in template syntax documentation
        if "template" in filepath.lower() and ("placeholder" in stripped.lower() or "convention" in stripped.lower()):
            continue
        # T3a.2 v2: skip when inside an inline-code span (documentation).
        if _match_inside_code_span(line, start, match.end()):
            continue
        findings.append(ScanFinding(
            file=filepath,
            line=line_num,
            category="unresolved-placeholder",
            severity="medium",
            message=f"Unresolved placeholder: {full_match}",
            snippet=stripped,
        ))

    # Unresolved manual placeholders
    for match in _UNRESOLVED_MANUAL_RE.finditer(line):
        if _match_inside_code_span(line, match.start(), match.end()):
            continue
        findings.append(ScanFinding(
            file=filepath,
            line=line_num,
            category="unresolved-manual",
            severity="low",
            message=f"Unresolved manual placeholder: {match.group(0)}",
            snippet=stripped,
        ))


def _token_entropy(token: str) -> float:
    """Return Shannon entropy for a token string."""
    if not token:
        return 0.0
    counts: dict[str, int] = {}
    for char in token:
        counts[char] = counts.get(char, 0) + 1
    total = len(token)
    entropy = 0.0
    for count in counts.values():
        probability = count / total
        entropy -= probability * __import__("math").log2(probability)
    return entropy
