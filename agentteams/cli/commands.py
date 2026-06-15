"""
commands.py — convert / interop / bridge CLI sub-command runners.

Extracted verbatim from build_team.py (CH-07 modular structure). build_team
re-exports these so main resolves them unchanged. Migrate/revert runners stay
in build_team (they self-invoke main). Gate calls route through the
security_gate module (Step A), so moving these does not affect gate patching.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agentteams.cli import security_gate
from agentteams.frameworks.registry import FRAMEWORKS

def _run_convert(
    source_dir: Path,
    target_framework: str,
    output: Path | None,
    dry_run: bool,
    overwrite: bool,
) -> int:
    """Execute the --convert-from path: convert an existing team to a new framework format.

    Args:
        source_dir: Directory containing the source agent files.
        target_framework: Target framework identifier.
        output: Explicit output directory, or None to auto-derive from source.
        dry_run: When True, report actions without writing files.
        overwrite: When True, overwrite existing target files.

    Returns:
        0 on success, 1 on error.
    """
    from agentteams.convert import convert_team

    if output is not None:
        target_dir = output
    else:
        # Auto-derive: use source parent as project root, place agents under framework dir
        project_root = source_dir.parent.parent  # e.g. /repo from /repo/.github/agents
        adapter_cls = FRAMEWORKS[target_framework]
        target_dir = adapter_cls().get_agents_dir(project_root)

    if not dry_run:
        from agentteams import security_refs as _security_refs

        convert_security = _security_refs.build_security_placeholders(
            output_dir=target_dir,
            offline=False,
            max_items=1,
            tools=None,
            skip_nvd=True,
        )
        security_gate._assert_security_intelligence_fresh(convert_security, output_dir=target_dir)

    dry_label = " (dry-run)" if dry_run else ""
    print(
        f"Converting{dry_label} agent team:\n"
        f"  source:  {source_dir}\n"
        f"  target:  {target_dir}\n"
        f"  framework: {target_framework}"
    )

    # Read project_name from build-log.json if present (best-effort)
    build_log_path = source_dir / "references" / "build-log.json"
    project_manifest: dict = {}
    if build_log_path.exists():
        try:
            import json as _json
            with build_log_path.open("r", encoding="utf-8") as fh:
                log = _json.load(fh)
            if isinstance(log.get("project_name"), str):
                project_manifest["project_name"] = log["project_name"]
        except (OSError, _json.JSONDecodeError):
            # CH-24: optional build-log read for a fallback project_name; a
            # missing/corrupt file is the known-recoverable case (use default).
            pass

    try:
        result = convert_team(
            source_dir=source_dir,
            target_dir=target_dir,
            target_framework=target_framework,
            project_manifest=project_manifest,
            dry_run=dry_run,
            overwrite=overwrite,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.errors:
        print(f"\n  ✗  {len(result.errors)} error(s):")
        for err in result.errors:
            print(f"    {err}")

    verb = "Would convert" if dry_run else "Converted"
    print(
        f"\n  {verb} {len(result.converted)} file(s)"
        + (f", skipped {len(result.skipped)}" if result.skipped else "")
        + "."
    )
    if dry_run and result.converted:
        print("  Files that would be written:")
        for path in result.converted:
            print(f"    {path}")

    return 0 if result.success else 1
def _run_interop(
    source_dir: Path,
    source_framework: str | None,
    target_framework: str,
    output: Path | None,
    mode: str,
    dry_run: bool,
    overwrite: bool,
) -> int:
    """Execute the --interop-from path via CAI normalization pipeline."""
    from agentteams.interop import detect_framework, run_interop

    detected = source_framework or detect_framework(source_dir)
    if output is not None:
        target_dir = output
    else:
        project_root = source_dir.parent.parent
        target_dir = FRAMEWORKS[target_framework]().get_agents_dir(project_root)

    if not dry_run:
        from agentteams import security_refs as _security_refs

        interop_security = _security_refs.build_security_placeholders(
            output_dir=target_dir,
            offline=False,
            max_items=1,
            tools=None,
            skip_nvd=True,
        )
        security_gate._assert_security_intelligence_fresh(interop_security, output_dir=target_dir)

    dry_label = " (dry-run)" if dry_run else ""
    print(
        f"Running interop{dry_label}:\n"
        f"  source:  {source_dir}\n"
        f"  source framework: {detected}\n"
        f"  target:  {target_dir}\n"
        f"  target framework: {target_framework}\n"
        f"  mode: {mode}"
    )

    try:
        result = run_interop(
            source_dir=source_dir,
            source_framework=detected,
            target_framework=target_framework,
            target_dir=target_dir,
            mode=mode,
            dry_run=dry_run,
            overwrite=overwrite,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.errors:
        print(f"\n  ✗  {len(result.errors)} error(s):")
        for err in result.errors:
            print(f"    {err}")

    verb = "Would interop-convert" if dry_run else "Interop-converted"
    print(
        f"\n  {verb} {len(result.converted)} file(s)"
        + (f", skipped {len(result.skipped)}" if result.skipped else "")
        + "."
    )
    if result.bundle_files:
        bundle_verb = "Would write" if dry_run else "Wrote"
        print(f"  {bundle_verb} {len(result.bundle_files)} interop bundle file(s).")

    return 0 if result.success else 1
_BRIDGE_AGENTS_DIR_SUFFIXES: dict[str, tuple[tuple[str, ...], ...]] = {
    "copilot-vscode": ((".github", "agents"),),
    "copilot-cli": ((".github", "copilot"),),
    "claude": ((".claude", "agents"),),
}
def _normalize_bridge_output_root(output: Path, target_framework: str) -> Path:
    """Strip a known agents-dir suffix from a bridge --output path.

    Bridge mode treats --output as the *repo root*; if a user passes the
    target framework's conventional agents directory (e.g. ``.github/agents``
    for copilot-vscode), strip the suffix and emit a warning so bridge
    artifacts do not land at nested ``.github/.github/...`` paths.
    """
    suffixes = _BRIDGE_AGENTS_DIR_SUFFIXES.get(target_framework, ())
    parts = output.parts
    for suffix in suffixes:
        if len(parts) >= len(suffix) and parts[-len(suffix):] == suffix:
            normalized = Path(*parts[:-len(suffix)]) if parts[:-len(suffix)] else Path(output.anchor or ".")
            print(
                f"Warning: bridge --output {output} ends in '{'/'.join(suffix)}'.\n"
                f"  Bridge mode treats --output as the repository root, not the\n"
                f"  agents directory. Normalizing to {normalized} so bridge\n"
                f"  artifacts are written under the expected layout.",
                file=sys.stderr,
            )
            return normalized
    return output
def _run_bridge(
    source_dir: Path,
    source_framework: str | None,
    target_framework: str,
    output: Path | None,
    dry_run: bool,
    overwrite: bool,
    check_only: bool,
    merge_only: bool = False,
    emit_skills: bool = True,
    host_features: list[str] | None = None,
) -> int:
    """Execute the --bridge-from path via lightweight compatibility artifacts."""
    from agentteams.bridge import run_bridge
    from agentteams.interop import detect_framework

    detected = source_framework or detect_framework(source_dir)
    if output is not None:
        output_root = _normalize_bridge_output_root(output, target_framework)
    else:
        project_root = source_dir.parent.parent
        output_root = project_root

    if not dry_run and not check_only:
        from agentteams import security_refs as _security_refs

        bridge_security = _security_refs.build_security_placeholders(
            output_dir=output_root,
            offline=False,
            max_items=1,
            tools=None,
            skip_nvd=True,
        )
        security_gate._assert_security_intelligence_fresh(bridge_security, output_dir=output_root)

    dry_label = " (dry-run)" if dry_run else ""
    print(
        f"Running bridge{dry_label}:\n"
        f"  source:  {source_dir}\n"
        f"  source framework: {detected}\n"
        f"  target framework: {target_framework}\n"
        f"  output root: {output_root}\n"
        f"  mode: {'check' if check_only else 'generate'}"
    )

    try:
        result = run_bridge(
            source_dir=source_dir,
            source_framework=detected,
            target_framework=target_framework,
            output_root=output_root,
            dry_run=dry_run,
            overwrite=overwrite,
            check_only=check_only,
            merge_only=merge_only,
            emit_skills=emit_skills,
            host_features=host_features or [],
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.errors:
        print(f"\n  ✗  {len(result.errors)} error(s):")
        for err in result.errors:
            print(f"    {err}")

    if check_only:
        print(f"\n  Bridge check: {'PASS' if result.check_ok else 'FAIL'}")
        if result.check_report_path:
            print(f"  Report: {result.check_report_path}")
        if result.manifest_missing:
            print(
                "\n  Hint: no bridge manifest exists yet. Run the same command "
                "with --bridge-refresh (omit --bridge-check) to generate the "
                "initial bridge artifacts, then re-run --bridge-check.",
                file=sys.stderr,
            )
    else:
        verb = "Would write" if dry_run else "Wrote"
        print(
            f"\n  {verb} {len(result.written)} bridge file(s)"
            + (f", skipped {len(result.skipped)}" if result.skipped else "")
            + "."
        )
        for notice in result.notices:
            print(f"  Notice: {notice}", file=sys.stderr)

    return 0 if result.success else 1
