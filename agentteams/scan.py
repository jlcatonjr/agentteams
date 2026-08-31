"""
scan.py — Proactive security scanner for generated agent files.

Scans .agent.md and related files in a generated agents directory for:
  - Absolute paths containing usernames (PII exposure)
  - Credential patterns (API keys, tokens, passwords)
    - High-entropy secret-like tokens in sensitive contexts
  - Unresolved auto-placeholders ({UPPER_SNAKE_CASE} tokens)
  - Unresolved manual placeholders ({MANUAL:*} still present after setup)

Also exposes ``python -m agentteams.scan <path>`` so a runtime with shell/``execute``
access but no way to natively ``import`` and call ``scan_content`` directly can invoke
the scanner at review time (not just at generation time via ``--scan-security``), and
``verdict_for_findings()`` — a pure mapping from scan findings to the HALT/CONDITIONAL
PASS/PASS verdict for the scan-derivable subset of security.template.md's escalation table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.backup import BACKUP_DIR_NAME as _BACKUP_DIR_NAME

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

#: Minimum Shannon entropy (bits/char) for an opaque token to be treated as a
#: possible secret. Named so the gate and the path-shape guard below share one
#: source of truth and cannot drift.
_ENTROPY_MIN_BITS = 3.8

#: High-entropy tokens that look like opaque secrets when they are contiguous
#: opaque strings rather than human-readable identifiers.
_HIGH_ENTROPY_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/=])"
    r"(?=[A-Za-z0-9+/=]{24,}(?![A-Za-z0-9+/=]))"
    r"(?=[A-Za-z0-9+/=]*[A-Za-z])"
    r"(?=[A-Za-z0-9+/=]*\d)"
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

    @property
    def verdict(self) -> str:
        """Return the HALT/CONDITIONAL_PASS/PASS verdict for this report's findings."""
        return verdict_for_findings(self.findings)


HALT = "HALT"
CONDITIONAL_PASS = "CONDITIONAL_PASS"
PASS = "PASS"


def verdict_for_findings(findings: Iterable[ScanFinding]) -> str:
    """Map scan findings to the security template's HALT/CONDITIONAL PASS/PASS verdict.

    Covers the scan-derivable subset of security.template.md's escalation table. Since
    2026-07-31 that includes Rule S-5's literal instruction-override patterns; what remains
    out of scope is S-5's third bullet (a heading that redefines agent identity), which needs
    judgment rather than matching
    (credential, PII-path, machine-specific-info, unresolved-placeholder findings).
    Procedural findings in that table (bulk destructive ops, external-repo writes,
    injection attempts) aren't derivable from a static content scan and remain the
    agent's own judgment call.
    """
    severities = {f.severity for f in findings}
    if "high" in severities:
        return HALT
    if severities:
        return CONDITIONAL_PASS
    return PASS


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
        expected_agent_names: When provided, `.agent.md` files whose basename is NOT in
            this set are reported as `orphan-agent` findings at `medium` severity — and are
            still scanned. They were skipped entirely until 2026-08-06, which made an
            injected agent file the one class of agent file the scanner never read (probe
            B11). `medium` is deliberate: non-blocking under `verdict_for_findings`, so the
            daily pipeline is not gated on stale-taxonomy leftovers, but no longer silent.

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
    #
    # Anchored to the ONE backup root this run would use, not to any directory named
    # `.agentteams-backups` at any depth. The `_BACKUP_DIR_NAME not in p.parts` form let an
    # agent with write access create a directory of that name anywhere and drop a payload into
    # a permanent scanner blind spot (probe B10). The rationale above — "already surfaced when
    # that content was live" — is only true of the real backup root, which is precisely the
    # provenance the name alone cannot establish.
    backup_root = (agents_dir / _BACKUP_DIR_NAME).resolve()
    kept: list[Path] = []
    for p in files_to_scan:
        # No try/except here (CH-24): these paths came from rglob so they exist, and resolve()
        # is non-strict. The one thing that would still raise is a symlink loop — which is a
        # real condition worth surfacing, not one to swallow into a skipped file.
        resolved = p.resolve()
        if resolved.is_relative_to(backup_root):
            continue
        if _BACKUP_DIR_NAME in p.parts:
            # A backup-named directory somewhere OTHER than the real backup root. Scan it, and
            # say why it drew attention.
            report.findings.append(ScanFinding(
                file=str(p), line=1, category="scanner-evasion", severity="medium",
                message=(f"directory named {_BACKUP_DIR_NAME!r} outside the run's backup root "
                         f"({backup_root}) — scanned rather than skipped"),
                snippet="",
            ))
        kept.append(p)
    files_to_scan = kept

    # T3a.2 v4: orphan .agent.md files -- present on disk, absent from the current team's
    # manifest. These used to be dropped from the scan entirely, on the reasoning that the
    # orphan advisory in build_team already surfaces them. But an INJECTED agent file is
    # exactly the case least likely to appear in a manifest, so the one class of agent file
    # most worth scanning was the one class exempted (probe B11). They are scanned now, and
    # their orphan status is itself reported at `medium` -- non-blocking, so the daily
    # pipeline still is not gated on stale-taxonomy leftovers, but no longer invisible.
    if expected_agent_names is not None:
        for p in files_to_scan:
            if p.name.endswith(".agent.md") and p.name not in expected_agent_names:
                report.findings.append(ScanFinding(
                    file=str(p), line=1, category="orphan-agent", severity="medium",
                    message=("agent file not in the current team manifest — a stale taxonomy "
                             "leftover, or a file nothing generated"),
                    snippet="",
                ))

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
        filename: Filename for reporting purposes, and for the provenance decisions in
            :func:`_is_module_owned_path`. **A path the module does not own gets no
            exemptions** — the default is strict, so an unknown or synthetic filename cannot
            inherit the suppressions that exist for this module's own documentation.

    Returns:
        List of ScanFinding objects.
    """
    findings: list[ScanFinding] = []
    module_owned = _is_module_owned_path(filename)
    lines = content.splitlines()
    previous_norm = ""
    for line_num, line in enumerate(lines, start=1):
        norm = _normalize_for_matching(line)
        _check_injection(
            norm, line_num, filename, findings,
            in_front_matter=_line_in_front_matter(lines, line_num),
            module_owned=module_owned,
            previous_line=previous_norm,
            raw_line=line,
        )
        _check_line(line, line_num, filename, findings)
        previous_norm = norm
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
    "security-vulnerability-watch.json",
})

#: Directory, relative to the scanned agents dir, where the pipeline writes those files.
#:
#: The exemption was keyed on BASENAME and applied via `rglob`, so any file anywhere in the
#: tree named `memory-index.json` inherited it — an agent with write access could pre-blind the
#: scanner to its own payload simply by choosing the filename (probe B9, the LLM08
#: retrieval-poisoning path). Anchoring to the one directory the pipeline actually writes them
#: to makes the exemption a statement about provenance rather than about spelling.
_OPERATIONAL_JSON_DIR = "references"


def _is_operational_json(file_path: Path, agents_dir: Path) -> bool:
    """True when *file_path* is a pipeline-written operational JSON file.

    Requires BOTH the known basename AND residence directly in the agents dir's
    ``references/`` directory. A file that satisfies only one is scanned normally.
    """
    if file_path.name not in _OPERATIONAL_JSON_NAMES:
        return False
    try:
        return file_path.resolve().parent == (agents_dir / _OPERATIONAL_JSON_DIR).resolve()
    except OSError:
        return False


#: Unicode categories stripped before pattern matching. `Cf` is the format-character class —
#: zero-width space, zero-width joiner, the bidi overrides, the TAG block. None of them render,
#: all of them break a literal `str.find`, and a model reading the file sees the phrase intact.
#: Measured 2026-08-06 (probe B3): one U+200B inside "ignore previous instructions" defeated
#: Rule S-5 entirely.
_FORMAT_CHAR_CATEGORY = "Cf"


def _normalize_for_matching(line: str) -> str:
    """Return *line* canonicalised for literal pattern matching.

    Two transforms, in order:

    1. **NFKC** — folds compatibility forms (fullwidth Latin, ligatures, styled math letters)
       onto their plain equivalents, so a payload written in fullwidth characters matches.
    2. **Format-character removal** — drops every `Cf` codepoint.

    Offsets shift as a result, which is why every caller matches *and* runs its code-span test
    against this same normalised string. Only the reported snippet comes from the original
    line, so a finding still quotes what a human would see in the file.

    NFKC deliberately, not NFKD: NFKD would decompose accented characters into base + combining
    mark, which changes lengths without helping, since no attack in this class depends on it.
    """
    normalized = unicodedata.normalize("NFKC", line)
    return "".join(
        ch for ch in normalized if unicodedata.category(ch) != _FORMAT_CHAR_CATEGORY
    )


def _line_in_front_matter(lines: list[str], line_num: int) -> bool:
    """True when 1-indexed *line_num* falls inside a genuine YAML front-matter block.

    Front matter is front matter only when the opening ``---`` is the file's **first line**.
    The previous implementation counted the first two ``---`` lines appearing anywhere, which
    a reviewed document controls: two markdown horizontal rules forged a front-matter block,
    and every identity-override phrase between them was suppressed (probe B5). YAML has no
    such flexibility — a front-matter block that does not start at byte 0 is not front matter
    to any parser either, so this is a correctness fix as much as a security one.
    """
    if not lines or lines[0].strip() != "---":
        return False
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return 2 <= line_num <= index          # between the delimiters, exclusive
    return False


#: Path fragments identifying files THIS MODULE authors or ships. Only these inherit the
#: code-span exemption in :func:`_check_injection`.
#:
#: The exemption exists so `security.template.md` does not HALT on its own Rule S-5 text, which
#: lists every override phrase in backticks. That is legitimate for the module's own
#: documentation and indefensible as a blanket rule: a 2026-08-06 probe (B1) wrapped a live
#: payload in backticks and the scanner fell silent. Backticks are formatting — the reading
#: model still sees the words. So the exemption is now keyed on *who wrote the file* rather
#: than on *what the text looks like*, and reviewed content from outside the module gets none.
#: `.github/copilot/` is retained deliberately even though copilot-cli's active output
#: moved to `.github/agents/` (2026-08-15 convergence): this module authored the
#: pre-convergence files still sitting there in already-deployed repos, and the
#: exemption is keyed on authorship, not on where new output currently lands.
#: One fragment per framework output directory. A generated reference (e.g. the
#: instruction-authority reference, which quotes attack phrases as teaching examples) is
#: module-owned wherever it lands, so every framework's agents dir must appear here — an
#: omission silently drops the code-span exemption for that framework's derived repos.
_MODULE_OWNED_PATH_FRAGMENTS: tuple[str, ...] = (
    "agentteams/templates/",  # the module's own template library
    ".github/agents/",        # copilot-vscode
    ".claude/agents/",        # claude
    ".github/copilot/",       # copilot-cli
    ".goose/recipes/",        # goose
    ".agents/",               # codex + agents-md
)


def _is_module_owned_path(filename: str) -> bool:
    """True when *filename* is a file this module authors, ships, or generates.

    Args:
        filename: Path as supplied to the scan entry point. Separators are normalised so a
            Windows path is classified the same way.

    Returns:
        Whether documentation exemptions apply. Unknown, relative, or synthetic names
        (``<string>``, ``<stdin>``) return False — strict by default.
    """
    normalized = filename.replace("\\", "/")
    return any(fragment in normalized for fragment in _MODULE_OWNED_PATH_FRAGMENTS)


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

    is_operational_json = _is_operational_json(file_path, agents_dir)
    module_owned = _is_module_owned_path(rel_path)
    lines = content.splitlines()
    previous_norm = ""
    for line_num, line in enumerate(lines, start=1):
        norm = _normalize_for_matching(line)
        _check_injection(
            norm, line_num, rel_path, report.findings,
            in_front_matter=_line_in_front_matter(lines, line_num),
            module_owned=module_owned,
            previous_line=previous_norm,
            raw_line=line,
        )
        _check_line(
            line, line_num, rel_path, report.findings,
            skip_pii_path=is_operational_json,
            skip_entropy=is_operational_json,
            skip_placeholders=is_operational_json,
        )
        previous_norm = norm


#: Rule S-5's literal instruction-override patterns, verbatim from security.template.md. These
#: are exact strings, so they ARE scan-derivable — the module previously declined S-5 wholesale as
#: "not scan-derivable", which was true only of S-5's third bullet (a heading that *redefines
#: agent identity*, which needs judgment). Bullets one and two are string matching, and declining
#: them left a static check undone that the template says must happen.
#:
#: Project-supplied text is rendered into agent files that a model later reads as instruction, so
#: a match here is a real injection vector rather than a stylistic concern.
_INJECTION_PATTERNS: tuple[str, ...] = (
    "ignore previous instructions",
    "ignore all instructions",
    "disregard the above",
    "new instructions:",
    "system override:",
    "security bypass:",
)

#: Identity-override phrases (S-5 bullet two). Legitimate inside agent YAML front matter — a
#: template that says `description: "act as a reviewer"` is describing a role, not overriding one
#: — so the caller suppresses these while inside front matter.
_IDENTITY_OVERRIDE_PATTERNS: tuple[str, ...] = (
    "you are now",
    "your new role is",
)

#: Precedence claims — content asserting for itself an authority tier it cannot hold (C-1 in
#: ``references/instruction-authority.reference.md``). Distinct from the identity-override
#: phrases above: those claim to change *who the agent is*, these claim to change *where the
#: text ranks*. Read content sits at the bottom of the ordering by construction, so any such
#: claim inside it is the finding rather than an instruction.
#:
#: Deliberately literal and few, matching the conservative style of the lists above. A precedence
#: claim expressed in novel wording will not match, and that is the accepted trade: a scanner
#: that guesses at intent produces false positives on ordinary prose about precedence — of which
#: this project's own templates now contain a great deal — and a noisy security check gets muted.
_TIER_CLAIM_PATTERNS: tuple[str, ...] = (
    "supersedes all prior instructions",
    "supersedes all previous instructions",
    "takes precedence over all instructions",
    "override all prior instructions",
)


def _find_pattern(
    line: str, pattern: str, *, module_owned: bool
) -> int:
    """Return the index of *pattern* in *line*, or -1 when absent or legitimately quoted.

    The code-span exemption applies only to files this module owns. See
    :data:`_MODULE_OWNED_PATH_FRAGMENTS` for why quoting cannot be trusted in reviewed content.
    """
    idx = line.lower().find(pattern)
    if idx == -1:
        return -1
    if module_owned and _match_inside_code_span(line, idx, idx + len(pattern)):
        return -1
    return idx


def _find_pattern_across_lines(
    previous_line: str, line: str, pattern: str, *, module_owned: bool
) -> bool:
    """True when *pattern* spans the boundary between *previous_line* and *line*.

    Rule S-5 matched per line, so a payload split at a newline — "ignore previous\\ninstructions"
    — passed cleanly (probe B2). A reading model sees prose reflowed; the scanner saw two
    innocuous fragments.

    Whitespace is collapsed across the join so any line break, indentation, or list-marker
    wrapping is normalised away. Only the two-line window is considered: it covers the natural
    wrap and every observed payload, and widening it would start matching phrases assembled
    from unrelated paragraphs.

    The "not already on one line" test uses **raw containment**, not :func:`_find_pattern`.
    Using the latter conflated *absent* with *suppressed*: a module-owned doc that quotes
    ``ignore previous instructions`` in backticks has the phrase present-but-exempt on one
    line, `_find_pattern` returns -1 for it, and the join then reported a phantom
    "split across a line break" on the module's own Rule S-5 text. Raw containment asks the
    right question — is this pattern whole on either line? — and only a genuine straddle
    survives it.

    ``module_owned`` deliberately does not suppress a genuine straddle. The
    instruction-authority reference already forbids a code span wrapping across a line break
    for exactly this reason, so a real split inside a module-owned file is a formatting defect
    worth surfacing, not a false positive.
    """
    if not previous_line:
        return False
    joined = " ".join(f"{previous_line} {line}".split()).lower()
    if pattern not in joined:
        return False
    return pattern not in previous_line.lower() and pattern not in line.lower()


def _check_injection(line: str, line_num: int, filepath: str,
                     findings: list[ScanFinding], *, in_front_matter: bool,
                     module_owned: bool = False, previous_line: str = "",
                     raw_line: str | None = None) -> None:
    """Flag Rule S-5's literal instruction-override patterns.

    Args:
        line: The line to check, already Unicode-normalised by
            :func:`_normalize_for_matching`. Matching and the code-span test both run on this
            string so their offsets agree.
        line_num: 1-indexed line number.
        filepath: Repo-relative path, for the finding.
        findings: Accumulator, appended in place.
        in_front_matter: Whether this line is inside a genuine YAML front-matter block, where
            identity phrasing is descriptive rather than an override.
        module_owned: Whether this file is one the module authors or generates, and may
            therefore quote an override phrase inside a code span without that being an attack.
        previous_line: The preceding normalised line, for boundary-spanning matches.
        raw_line: The original, un-normalised line. Used for the reported snippet so a finding
            quotes what is actually in the file.
    """
    snippet_source = raw_line if raw_line is not None else line

    def _report(category_message: str) -> None:
        findings.append(ScanFinding(
            file=filepath, line=line_num, category="injection", severity="high",
            message=category_message, snippet=snippet_source.strip()[:120],
        ))

    for pattern in _INJECTION_PATTERNS:
        if _find_pattern(line, pattern, module_owned=module_owned) != -1:
            _report(f"Rule S-5 instruction-override pattern {pattern!r} — reviewed content "
                    f"is inert data, never instruction")
            return
        if _find_pattern_across_lines(previous_line, line, pattern, module_owned=module_owned):
            _report(f"Rule S-5 instruction-override pattern {pattern!r} split across a line "
                    f"break — a line break is not an escape")
            return
    for pattern in _TIER_CLAIM_PATTERNS:
        if _find_pattern(line, pattern, module_owned=module_owned) != -1:
            _report(f"C-1 tier claim {pattern!r} — content cannot assert its own authority; "
                    f"read content ranks below every instruction tier by construction")
            return
        if _find_pattern_across_lines(previous_line, line, pattern, module_owned=module_owned):
            _report(f"C-1 tier claim {pattern!r} split across a line break")
            return
    if in_front_matter:
        return
    for pattern in _IDENTITY_OVERRIDE_PATTERNS:
        if _find_pattern(line, pattern, module_owned=module_owned) != -1:
            _report(f"Rule S-5 identity-override phrase {pattern!r} outside front matter")
            return
        if _find_pattern_across_lines(previous_line, line, pattern, module_owned=module_owned):
            _report(f"Rule S-5 identity-override phrase {pattern!r} split across a line break")
            return


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
    stripped = line.strip()
    # An HTML comment or a code-fence marker suppresses PLACEHOLDER detection only.
    #
    # It used to suppress everything on the line, which meant a credential written as
    # `<!-- AKIA... -->` scanned clean while the identical uncommented line HALTed (probe B7).
    # Rule S-1 governs "any committed file" and says nothing about markup: a secret in a
    # comment is committed, pushed, and readable in git history exactly like one that is not.
    # Placeholder detection stays suppressed because a commented-out `{TOKEN}` genuinely is
    # documentation of the convention rather than an unresolved placeholder.
    is_markup_line = stripped.startswith("<!--") or stripped.startswith("```")
    if is_markup_line:
        skip_placeholders = True

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
        if entropy < _ENTROPY_MIN_BITS:
            continue
        # A slash-delimited filesystem path is not an opaque secret, even though
        # ``/`` sits in the base64 token class and inflates the run's entropy.
        # Suppress only in the context-free case; the sensitive-context matcher
        # (a secret keyword is on the line) keeps its wider net deliberately.
        if not secret_context and _looks_like_filesystem_path(token):
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


def _looks_like_filesystem_path(token: str) -> bool:
    """True when a high-entropy candidate is really a slash-delimited file path
    rather than an opaque secret.

    The context-free entropy detector's token class includes ``/`` because it is
    part of the base64 alphabet. That lets a deep repository path such as
    ``research/workflowTesting/Projects/AgentInfrastructureScoring/04`` match as a
    single 63-char "token" whose Shannon entropy clears the threshold — even
    though every ``/``-delimited segment is a human-readable identifier and none
    is a secret. This recurs for any sufficiently nested path whose slash-joined
    run reaches a digit-bearing segment (a numbered file, a dated folder), so it
    is a systematic false positive, not a one-off.

    A genuine base64 blob does not decompose this way: an incidental ``/`` inside
    it still leaves at least one segment that is itself an opaque high-entropy
    run, and base64 padding/index chars (``+``, ``=``) never appear in a
    filesystem path. Suppress (return ``True``) only when ALL hold:

      * the token contains at least one ``/``;
      * it contains no ``+`` or ``=`` (their presence signals base64, not a path);
      * no ``/``-delimited segment, re-tested on its own, independently qualifies
        as a high-entropy opaque token.

    The final clause is what keeps a real secret embedded as a single path
    component (e.g. ``creds/AKIA...9EXAMPLEkeyBLOB/use``) flagged: that segment
    still matches on its own, so the token is not suppressed. Applied only to the
    context-free detector; the sensitive-context matcher (keyword lines) keeps its
    wider net deliberately.
    """
    if "/" not in token or "+" in token or "=" in token:
        return False
    for segment in token.split("/"):
        if not segment:
            continue
        match = _HIGH_ENTROPY_TOKEN_RE.search(segment)
        if match is not None and _token_entropy(match.group(0)) >= _ENTROPY_MIN_BITS:
            return False
    return True


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


def main(argv: list[str] | None = None) -> int:
    """``python -m agentteams.scan <path>`` — scan a file's current content, or ``-`` for stdin.

    Exists for a runtime with shell/``execute`` access but no way to natively ``import`` and
    call ``scan_content`` directly — mirrors ``agentteams.research``'s ``__main__`` rationale.
    Prints JSON: ``{"findings": [...], "verdict": "HALT"|"CONDITIONAL_PASS"|"PASS"}``.
    Exit code 1 iff verdict is HALT (mirrors --scan-security's high_count gate), else 0.
    """
    parser = argparse.ArgumentParser(prog="python -m agentteams.scan")
    parser.add_argument("path", help="File to scan, or '-' to read stdin")
    args = parser.parse_args(argv)

    content = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    filename = "<stdin>" if args.path == "-" else args.path
    findings = scan_content(content, filename=filename)
    verdict = verdict_for_findings(findings)
    json.dump(
        {"findings": [f.__dict__ for f in findings], "verdict": verdict},
        sys.stdout, indent=2,
    )
    sys.stdout.write("\n")
    return 1 if verdict == HALT else 0


if __name__ == "__main__":
    raise SystemExit(main())
