"""
scan.py — Proactive security scanner for generated agent files.

Scans .agent.md and related files in a generated agents directory for:
  - Absolute paths containing usernames (PII exposure)
  - Credential patterns (API keys, tokens, passwords)
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

#: Absolute paths with username components (macOS, Linux, Windows)
_PII_PATH_RE = re.compile(
    r"(/Users/[a-zA-Z][a-zA-Z0-9._-]+/"
    r"|/home/[a-zA-Z][a-zA-Z0-9._-]+/"
    r"|[A-Z]:\\Users\\[a-zA-Z][a-zA-Z0-9._-]+\\)"
)

#: Common credential patterns
_CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("API key", re.compile(r"(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{20,}", re.IGNORECASE)),
    ("Bearer token", re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Private key header", re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----")),
    ("Password assignment", re.compile(r"(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{4,}", re.IGNORECASE)),
    ("Connection string", re.compile(r"(?:postgres|mysql|mongodb)://[^\s]+:[^\s]+@", re.IGNORECASE)),
]

#: Unresolved auto-placeholder tokens {UPPER_SNAKE_CASE}
_UNRESOLVED_AUTO_RE = re.compile(r"\{([A-Z][A-Z0-9_]{2,})\}")

#: Unresolved manual placeholder tokens {MANUAL:NAME}
_UNRESOLVED_MANUAL_RE = re.compile(r"\{MANUAL:([A-Z][A-Z0-9_]*)\}")

#: Known safe tokens that look like placeholders but aren't
_SAFE_TOKENS = frozenset({
    "TRUE", "FALSE", "NULL", "NONE", "TODO", "FIXME", "NOTE",
    "TBD", "REQUIRED", "OPTIONAL", "DEPRECATED",
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

def scan_directory(agents_dir: Path) -> ScanReport:
    """Scan all agent files in a directory for security issues.

    Args:
        agents_dir: Path to the .github/agents/ directory.

    Returns:
        ScanReport with all findings.
    """
    report = ScanReport()

    # Scan .agent.md files and related files
    scan_patterns = ["*.agent.md", "*.md", "*.json"]
    files_to_scan: list[Path] = []
    for pattern in scan_patterns:
        files_to_scan.extend(agents_dir.rglob(pattern))

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

    for line_num, line in enumerate(content.splitlines(), start=1):
        _check_line(line, line_num, rel_path, report.findings)


def _check_line(
    line: str,
    line_num: int,
    filepath: str,
    findings: list[ScanFinding],
) -> None:
    """Check a single line for all security patterns."""
    # Skip markdown comments and code fence markers
    stripped = line.strip()
    if stripped.startswith("<!--") or stripped.startswith("```"):
        return

    # PII: absolute paths with usernames
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

    # Unresolved auto-placeholders (excluding known safe tokens)
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
        findings.append(ScanFinding(
            file=filepath,
            line=line_num,
            category="unresolved-manual",
            severity="low",
            message=f"Unresolved manual placeholder: {match.group(0)}",
            snippet=stripped,
        ))
