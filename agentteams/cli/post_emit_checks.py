"""post_emit_checks.py — checks that run after a generate/update has written its files.

Homed outside ``cli.generate`` because that module sits at the CH-07 ceiling and a check that
runs *after* the pipeline has no business being inside the pipeline function's module anyway.
Everything here takes the finished emit result and the tree on disk, and returns whether the run
should be promoted to a non-zero exit code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentteams import emit


def _post_emit_security_scan(
    args: argparse.Namespace,
    output_dir: Path,
    manifest: dict,
    result: emit.EmitResult,
) -> bool:
    """Scan the just-emitted tree for credentials, PII, and injection patterns.

    ``scan.scan_directory`` had exactly one call site: the ``--scan-security`` short-circuit,
    which returns before rendering. So the deterministic checks backing Rules S-1, S-5, S-6 and
    S-8 ran only when an operator remembered to ask, and never on the run that wrote the files.

    Advisory by default — high-severity findings are printed and the exit code is untouched, so a
    finding that predates this check cannot start failing a consumer's build for something they
    did not opt into. Blocking under ``--fleet``, matching the precedent that ``--fleet`` already
    forbids ``--shrink-policy=allow``: a fleet run writes to many repositories at once and is
    where a silent finding is least recoverable.

    Args:
        args: Parsed CLI namespace.
        output_dir: The agents output directory just written.
        manifest: Team manifest, used to scope the scan to expected agent files.
        result: Emit result; a dry run or a failed emit is not scanned.

    Returns:
        True when the run should be promoted to a non-zero exit code.
    """
    if getattr(args, "dry_run", False) or getattr(args, "scan_security", False):
        return False
    if not getattr(result, "success", False):
        return False

    from agentteams import scan as _scan

    expected = {
        Path(f["path"]).name
        for f in manifest.get("output_files", [])
        if isinstance(f, dict) and str(f.get("path", "")).endswith(".agent.md")
    }
    try:
        report = _scan.scan_directory(output_dir, expected_agent_names=expected or None)
    except OSError as exc:                     # a scan failure must never fail the emit
        print(f"  !  Security scan skipped: {exc}", file=sys.stderr)
        return False

    if report.high_count == 0:
        return False

    blocking = bool(getattr(args, "fleet", False))
    print(f"\n  [{'FAIL' if blocking else 'warn'}] Security scan: "
          f"{report.high_count} high-severity finding(s)")
    for finding in (f for f in report.findings if f.severity == "high"):
        print(f"      {finding.file}:{finding.line} [{finding.category}] {finding.message}")
    print("      Run `agentteams --scan-security` for the full report."
          + ("" if blocking else "  (advisory; --fleet makes this blocking)"))
    return blocking
