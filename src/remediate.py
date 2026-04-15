"""remediate.py — Auto-correction support via standalone Copilot CLI.

Provides an opt-in remediation pass that can run after post-audit finds
issues in generated agent team files. The remediation step uses the
standalone `copilot` CLI in non-interactive mode, scopes file access to the
generated team directory, and then returns control to the main pipeline for a
verification rerun.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_COPILOT_MODEL = "gpt-5.4"
_COPILOT_TIMEOUT_SECONDS = 180


@dataclass
class RemediationResult:
    """Outcome of a Copilot CLI remediation attempt."""

    attempted: bool
    succeeded: bool
    message: str
    command: list[str]
    stdout: str = ""
    stderr: str = ""


def run_copilot_autocorrect(
    *,
    output_dir: Path,
    manifest: dict[str, Any],
    audit_result: Any,
) -> RemediationResult:
    """Invoke the standalone Copilot CLI to repair generated team files.

    Args:
        output_dir: Root directory containing generated team files.
        manifest: Team manifest from analyze.build_manifest().
        audit_result: AuditResult-like object containing findings.

    Returns:
        RemediationResult describing whether the attempt ran and succeeded.

    Raises:
        OSError: Propagates unexpected OS-level subprocess failures.
    """
    findings = _collect_findings(audit_result)
    if not findings:
        return RemediationResult(
            attempted=False,
            succeeded=True,
            message="No audit findings to remediate.",
            command=[],
        )

    copilot_path = shutil.which("copilot")
    if not copilot_path:
        return RemediationResult(
            attempted=False,
            succeeded=False,
            message="Standalone `copilot` CLI not found on PATH.",
            command=[],
        )

    prompt = _build_copilot_prompt(
        output_dir=output_dir,
        manifest=manifest,
        findings=findings,
    )
    command = [
        copilot_path,
        "-p",
        prompt,
        "--allow-all-tools",
        "--no-ask-user",
        "--no-custom-instructions",
        "--model",
        _COPILOT_MODEL,
        "--silent",
        "--add-dir",
        str(output_dir),
        "--add-dir",
        str(output_dir.parent),
    ]

    try:
        proc = subprocess.run(
            command,
            cwd=output_dir.parent,
            capture_output=True,
            text=True,
            timeout=_COPILOT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return RemediationResult(
            attempted=True,
            succeeded=False,
            message="Copilot remediation timed out.",
            command=command,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
        )

    succeeded = proc.returncode == 0
    message = "Copilot remediation completed." if succeeded else "Copilot remediation failed."
    return RemediationResult(
        attempted=True,
        succeeded=succeeded,
        message=message,
        command=command,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def print_remediation_summary(result: RemediationResult) -> None:
    """Print a concise summary of the remediation attempt.

    Args:
        result: RemediationResult from run_copilot_autocorrect().

    Returns:
        None.

    Raises:
        None.
    """
    print("\n  --- Copilot Auto-Correction ---")
    if not result.attempted:
        prefix = "  ·"
    elif result.succeeded:
        prefix = "  ✓"
    else:
        prefix = "  ✗"

    print(f"{prefix}  {result.message}")
    if result.stderr.strip():
        print("     stderr:")
        for line in result.stderr.strip().splitlines()[:10]:
            print(f"       {line}")
    elif result.stdout.strip():
        print("     output:")
        for line in result.stdout.strip().splitlines()[:10]:
            print(f"       {line}")


def _collect_findings(audit_result: Any) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for attr in (
        "static_findings",
        "agent_refactor_findings",
        "code_hygiene_findings",
    ):
        for finding in getattr(audit_result, attr, []):
            findings.append({
                "severity": finding.severity,
                "code": finding.code,
                "file": finding.file,
                "description": finding.description,
            })
    return findings


def _build_copilot_prompt(
    *,
    output_dir: Path,
    manifest: dict[str, Any],
    findings: list[dict[str, str]],
) -> str:
    project_name = manifest.get("project_name", "unknown project")
    finding_lines = "\n".join(
        f"- [{item['severity']}] {item['code']} in {item['file']}: {item['description']}"
        for item in findings
    )
    return (
        f"Repair the generated agent team for project '{project_name}'. "
        f"Work only inside '{output_dir}' and '{output_dir.parent}'. "
        "Fix only the concrete audit findings listed below. Preserve existing style, "
        "do not create new documentation files, do not edit source templates or pipeline code, "
        "and do not invent values for unresolved MANUAL placeholders unless the value is directly "
        "inferable from files already in scope. Make the minimal edits required, then stop.\n\n"
        "Audit findings:\n"
        f"{finding_lines}\n"
    )