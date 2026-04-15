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

