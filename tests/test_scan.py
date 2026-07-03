"""
Tests for src/scan.py — security scanner for generated agent files.
"""

import pytest
from pathlib import Path
from agentteams.scan import scan_directory, scan_content, ScanReport, ScanFinding


# ---------------------------------------------------------------------------
# scan_content — PII detection
# ---------------------------------------------------------------------------

def test_detect_absolute_path_macos():
    content = "Path: /Users/johndoe/project/agents/file.md"
    findings = scan_content(content)
    assert any(f.category == "PII" for f in findings)


def test_detect_absolute_path_linux():
    content = "Path: /home/johndoe/project/file.md"
    findings = scan_content(content)
    assert any(f.category == "PII" for f in findings)


def test_detect_absolute_path_windows():
    content = r"Path: C:\Users\johndoe\project\file.md"
    findings = scan_content(content)
    assert any(f.category == "PII" for f in findings)


def test_no_pii_in_clean_content():
    content = "Use `~/project/` for relative paths."
    findings = scan_content(content)
    assert not any(f.category == "PII" for f in findings)


# ---------------------------------------------------------------------------
# scan_content — credential detection
# ---------------------------------------------------------------------------

def test_detect_api_key():
    content = "api_key: sk_live_abc1234567890123456789"
    findings = scan_content(content)
    assert any(f.category == "credential" for f in findings)


def test_detect_bearer_token():
    content = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
    findings = scan_content(content)
    assert any(f.category == "credential" for f in findings)


def test_detect_aws_key():
    content = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"
    findings = scan_content(content)
    assert any(f.category == "credential" for f in findings)


def test_detect_private_key():
    content = "-----BEGIN RSA PRIVATE KEY-----"
    findings = scan_content(content)
    assert any(f.category == "credential" for f in findings)


def test_no_credential_in_clean_content():
    content = "Use environment variables for secrets."
    findings = scan_content(content)
    assert not any(f.category == "credential" for f in findings)


def test_detect_high_entropy_secret_like_token():
    content = "client_secret = AbCdEfGhIjKlMnOpQrStUv1234567890"
    findings = scan_content(content)
    assert any(f.category == "credential" for f in findings)


def test_ignore_long_non_secret_text_without_sensitive_context():
    content = "reference_id = documentation-identifier-2026-05-10-brief"
    findings = scan_content(content)
    assert not any(f.category == "credential" for f in findings)


# ---------------------------------------------------------------------------
# scan_content — unresolved placeholders
# ---------------------------------------------------------------------------

def test_detect_unresolved_auto_placeholder():
    content = "Output: {PRIMARY_OUTPUT_DIR}"
    findings = scan_content(content)
    assert any(f.category == "unresolved-placeholder" for f in findings)


def test_detect_unresolved_manual_placeholder():
    content = "Ref DB: {MANUAL:REFERENCE_DB_PATH}"
    findings = scan_content(content)
    assert any(f.category == "unresolved-manual" for f in findings)


def test_safe_tokens_ignored():
    content = "Status: {TODO} {FIXME} {NOTE}"
    findings = scan_content(content)
    assert not any(f.category == "unresolved-placeholder" for f in findings)


# ---------------------------------------------------------------------------
# scan_directory
# ---------------------------------------------------------------------------

def test_scan_directory_clean(tmp_path):
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "test.agent.md").write_text("# Clean agent file\n\nNo issues here.", encoding="utf-8")

    report = scan_directory(agents_dir)
    assert report.scanned_files >= 1
    assert not report.has_issues


def test_scan_directory_with_issues(tmp_path):
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "bad.agent.md").write_text(
        "# Agent\n\nPath: /Users/johndoe/secret/project\n"
        "api_key: sk_live_abc1234567890123456789\n",
        encoding="utf-8",
    )

    report = scan_directory(agents_dir)
    assert report.has_issues
    assert report.high_count >= 2  # PII + credential


def test_scan_directory_empty(tmp_path):
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)

    report = scan_directory(agents_dir)
    assert report.scanned_files == 0
    assert not report.has_issues


# ---------------------------------------------------------------------------
# ScanReport properties
# ---------------------------------------------------------------------------

def test_report_severity_counts():
    report = ScanReport(scanned_files=1)
    report.findings = [
        ScanFinding("f.md", 1, "PII", "high", "msg", "snip"),
        ScanFinding("f.md", 2, "PII", "high", "msg", "snip"),
        ScanFinding("f.md", 3, "unresolved", "medium", "msg", "snip"),
        ScanFinding("f.md", 4, "manual", "low", "msg", "snip"),
    ]
    assert report.high_count == 2
    assert report.medium_count == 1
    assert report.low_count == 1


def test_medium_only_findings_do_not_raise_high_count():
    """Medium-severity findings (entropy false positives) must not set high_count.

    The --scan-security exit code uses high_count > 0, so medium findings
    (e.g. moderate-entropy tokens in generated markdown prose) must be
    informational-only and must not block CI.
    """
    report = ScanReport(scanned_files=2)
    report.findings = [
        ScanFinding("a.agent.md", 10, "credential", "medium", "High-entropy token detected", "snip"),
        ScanFinding("b.agent.md", 20, "credential", "medium", "High-entropy token detected", "snip"),
    ]
    assert report.medium_count == 2
    assert report.high_count == 0
    assert report.has_issues  # findings exist
    # --scan-security uses `high_count > 0` as exit-1 predicate
    assert not (report.high_count > 0)


# ---------------------------------------------------------------------------
# Additional credential patterns
# ---------------------------------------------------------------------------

def test_detect_password_assignment():
    """Password assignment pattern should be detected as credential."""
    content = 'password: "mysecret123"'
    findings = scan_content(content)
    assert any(f.category == "credential" for f in findings)


def test_detect_password_equals_form():
    """password = 'value' form should also be detected."""
    content = "password = 'my$ecret!'"
    findings = scan_content(content)
    assert any(f.category == "credential" for f in findings)


def test_detect_connection_string_postgres():
    content = "db_url = postgres://user:pass@localhost/mydb"
    findings = scan_content(content)
    assert any(f.category == "credential" for f in findings)


def test_detect_connection_string_mysql():
    content = "conn = mysql://admin:s3cret@db.example.com/shop"
    findings = scan_content(content)
    assert any(f.category == "credential" for f in findings)


def test_detect_connection_string_mongodb():
    content = "MONGO_URI = mongodb://root:password@mongo:27017/mydb"
    findings = scan_content(content)
    assert any(f.category == "credential" for f in findings)


# ---------------------------------------------------------------------------
# Line number accuracy
# ---------------------------------------------------------------------------

def test_scan_content_finding_line_number_is_accurate():
    """Credential on line 3 must report line == 3."""
    content = (
        "# Agent file\n"
        "\n"
        'password: "secretvalue"\n'
        "\n"
        "# End\n"
    )
    findings = scan_content(content)
    credential_findings = [f for f in findings if f.category == "credential"]
    assert len(credential_findings) >= 1
    assert credential_findings[0].line == 3


def test_scan_content_pii_line_number_accurate():
    """PII on line 4 must report line == 4."""
    content = (
        "# Header\n"
        "\n"
        "## Section\n"
        "Path: /Users/johndoe/project/\n"
        "\n"
    )
    findings = scan_content(content)
    pii_findings = [f for f in findings if f.category == "PII"]
    assert len(pii_findings) >= 1
    assert pii_findings[0].line == 4


# ---------------------------------------------------------------------------
# Non-.md files
# ---------------------------------------------------------------------------

def test_scan_directory_does_not_skip_md_files(tmp_path):
    """Scan must pick up .md files in the agents directory."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "readme.md").write_text(
        "api_key: sk_live_abc1234567890123456789\n", encoding="utf-8"
    )
    report = scan_directory(agents_dir)
    assert report.has_issues


def test_placeholder_inside_backticks_is_not_flagged(tmp_path):
    """T3a.2 v2: documentation placeholders inside `...` spans are skipped."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "doc.md").write_text(
        "Templates use `{UPPER_SNAKE_CASE}` for auto-resolved placeholders.\n",
        encoding="utf-8",
    )
    report = scan_directory(agents_dir)
    msgs = [f.message for f in report.findings if f.category == "unresolved-placeholder"]
    assert not msgs, f"backticked doc placeholder should not fire: {msgs}"


def test_placeholder_outside_backticks_still_flagged(tmp_path):
    """T3a.2 v2: a bare unresolved placeholder still fires."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "live.agent.md").write_text(
        "The reference path is {REFERENCE_DB_PATH} (not resolved).\n",
        encoding="utf-8",
    )
    report = scan_directory(agents_dir)
    msgs = [f.message for f in report.findings if f.category == "unresolved-placeholder"]
    assert any("REFERENCE_DB_PATH" in m for m in msgs)


def test_pii_in_operational_json_is_suppressed(tmp_path):
    """T3a.2 v2: delivery-receipt.json and friends are allowed to carry paths."""
    agents_dir = tmp_path / ".github" / "agents"
    refs = agents_dir / "references"
    refs.mkdir(parents=True)
    (refs / "delivery-receipt.json").write_text(
        '{"path": "/Users/jamescaton/githubrepositories/foo"}\n',
        encoding="utf-8",
    )
    report = scan_directory(agents_dir)
    pii = [f for f in report.findings if f.category == "PII"]
    assert not pii, f"operational JSON should not carry PII findings: {pii}"


def test_pii_in_regular_md_still_flagged(tmp_path):
    """T3a.2 v2: PII suppression is narrow to operational JSON names."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "live.agent.md").write_text(
        "Note: see /Users/jamescaton/githubrepositories/foo for details.\n",
        encoding="utf-8",
    )
    report = scan_directory(agents_dir)
    pii = [f for f in report.findings if f.category == "PII"]
    assert pii, "PII should still fire in regular .md files"


def test_word_bounded_secret_context_avoids_false_positive(tmp_path):
    """T3a.2 v3: 'tokenized' inside a test name should not trigger
    secret_context on the surrounding line (no long opaque token to flag).
    """
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "controls.reference.md").write_text(
        "| CTRL-06 | agentic-permission-boundaries | "
        "tests/test_build_team_security_gates.py::"
        "test_action_matches_tokenized_action_names | "
        "build_team.py action matching boundary | implemented |\n",
        encoding="utf-8",
    )
    report = scan_directory(agents_dir)
    cred = [f for f in report.findings if f.category == "credential"]
    assert not cred, f"prose 'tokenized' must not trip secret_context: {cred}"


def test_actual_secret_token_still_flagged(tmp_path):
    """T3a.2 v3: a real secret-shaped key in a real auth-shaped line still fires."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "leak.agent.md").write_text(
        "auth token: aZxQ9mPL3kFn5rT8wYbCdE6gHj1iU2sV4\n",
        encoding="utf-8",
    )
    report = scan_directory(agents_dir)
    cred = [f for f in report.findings if f.category == "credential"]
    assert cred, "real auth token line should still flag"


def test_scan_directory_skips_agentteams_backups(tmp_path):
    """T3a.2: backup snapshots under .agentteams-backups/ are not scanned.

    Live files with the same flagged content are still reported.
    """
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    flagged = "api_key: sk_live_abc1234567890123456789\n"

    backup_dir = agents_dir / ".agentteams-backups" / "20260420-222216"
    backup_dir.mkdir(parents=True)
    (backup_dir / "snapshot.agent.md").write_text(flagged, encoding="utf-8")

    (agents_dir / "live.agent.md").write_text(flagged, encoding="utf-8")

    report = scan_directory(agents_dir)
    flagged_paths = {f.file for f in report.findings}
    assert any("live.agent.md" in p for p in flagged_paths)
    assert not any(".agentteams-backups" in p for p in flagged_paths)


# ---------------------------------------------------------------------------
# SEC-01: Entropy detector false-positive regression tests
# ---------------------------------------------------------------------------

def test_entropy_fp_path_slug_with_downstream_digit():
    """SEC-01: A path-like string (>=24 chars, all letters+slashes) must NOT
    trigger the entropy detector when a digit appears later on the same line.

    Root cause: the old (?=.*\\d) lookahead scanned the full remaining line,
    so 'github/workflows/security' (25 chars, no digit) was flagged because
    '09' in a trailing timestamp satisfied (?=.*\\d).
    """
    # Mimics the real false-positive line from security-vulnerability-watch.json
    content = "github/workflows/security-maintenance.yml; scheduled 09:00 EDT"
    findings = scan_content(content)
    cred = [f for f in findings if f.category == "credential"]
    assert not cred, (
        f"Path slug with downstream digit should not trigger entropy detector: {cred}"
    )


def test_entropy_fp_all_digit_token_with_downstream_letter():
    """SEC-01: A 24-digit all-numeric token must NOT trigger the entropy
    detector when a letter appears later on the same line.

    Root cause: the old (?=.*[A-Za-z]) lookahead scanned the full remaining
    line, so a truncated numeric checksum was flagged because any letter
    elsewhere on the line satisfied the lookahead.

    Note: the separator before the token must not be in [A-Za-z0-9+/=] so
    that the token itself is isolated as all-digit (colon+space works).
    """
    content = "checksum: 012345678901234567890123 verified by admin"
    findings = scan_content(content)
    cred = [f for f in findings if f.category == "credential"]
    assert not cred, (
        f"All-digit token with downstream letter should not trigger entropy detector: {cred}"
    )


def test_entropy_genuine_mixed_token_still_flagged():
    """SEC-01: A genuine 24-char mixed alphanumeric token must still be
    detected even after the lookahead fix.
    """
    content = "client_secret = AbCdEfGhIjKlMnOpQrStUv12"
    findings = scan_content(content)
    cred = [f for f in findings if f.category == "credential"]
    assert cred, "Genuine mixed-alphanumeric secret token should still be flagged"

