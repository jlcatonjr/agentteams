#!/usr/bin/env python3
"""
build_team.py — CLI entry point for the Agent Teams Module.

Usage:
    python build_team.py --description brief.md --project /path/to/project
    python build_team.py --description brief.json --framework copilot-cli --dry-run
    python build_team.py --description brief.json --output /custom/output/dir

Options:
    --description  PATH   Project description file (.json or .md) [required]
    --project      PATH   Existing project directory to scan (overrides existing_project_path in description)
    --framework    NAME   Target framework: copilot-vscode (default), copilot-cli, claude
    --output       DIR    Output directory for agent files (default: <project>/.github/agents/)
    --dry-run             Show what would be generated without writing files
    --overwrite           Overwrite existing agent files without prompting
    --yes                 Non-interactive: answer yes to all prompts
    --no-scan             Disable project directory scanning
    --version             Print version and exit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the src/ package is importable regardless of install mode
_SCRIPT_DIR = Path(__file__).parent
if str(_SCRIPT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from src import ingest, analyze, render, emit
from src.frameworks.copilot_vscode import CopilotVSCodeAdapter
from src.frameworks.copilot_cli import CopilotCLIAdapter
from src.frameworks.claude import ClaudeAdapter

__version__ = "0.1.0"

FRAMEWORKS = {
    "copilot-vscode": CopilotVSCodeAdapter,
    "copilot-cli": CopilotCLIAdapter,
    "claude": ClaudeAdapter,
}

TEMPLATES_DIR = _SCRIPT_DIR / "templates"


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_team",
        description="Generate a complete agent team for any project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--description", "-d",
        metavar="PATH",
        required=True,
        help="Project description file (.json or .md)",
    )
    parser.add_argument(
        "--project", "-p",
        metavar="PATH",
        default=None,
        help="Project directory to scan and use as base (overrides existing_project_path in description)",
    )
    parser.add_argument(
        "--framework", "-f",
        choices=list(FRAMEWORKS.keys()),
        default="copilot-vscode",
        metavar="NAME",
        help=f"Target framework: {', '.join(FRAMEWORKS)} (default: copilot-vscode)",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="DIR",
        default=None,
        help="Output directory for agent files. Default: <project>/.github/agents/",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be generated without writing files",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing agent files without prompting",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Non-interactive: answer yes to all prompts",
    )
    parser.add_argument(
        "--no-scan",
        action="store_true",
        help="Disable project directory scanning",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    framework_id: str = args.framework
    adapter = FRAMEWORKS[framework_id]()

    # -----------------------------------------------------------------------
    # Step 1: Ingest
    # -----------------------------------------------------------------------
    print(f"Loading description from {args.description!r}...")
    try:
        description = ingest.load(args.description, scan_project=not args.no_scan)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error loading description: {exc}", file=sys.stderr)
        return 1

    # Override project path from CLI if provided
    if args.project:
        description["existing_project_path"] = str(Path(args.project).resolve())
        if not args.no_scan:
            description = ingest._supplement_from_directory(
                description, Path(description["existing_project_path"])
            )

    # -----------------------------------------------------------------------
    # Step 2: Validate
    # -----------------------------------------------------------------------
    errors = ingest.validate(description)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    # -----------------------------------------------------------------------
    # Step 3: Analyze → manifest
    # -----------------------------------------------------------------------
    print(f"Analyzing project for {framework_id!r} framework...")
    manifest = analyze.build_manifest(description, framework=framework_id)

    project_name = manifest["project_name"]
    project_type = manifest["project_type"]
    print(f"  Project: {project_name!r}  |  Type: {project_type}  |  Framework: {framework_id}")
    print(f"  Archetypes: {', '.join(manifest['selected_archetypes'])}")
    print(f"  Components: {len(manifest['components'])}")
    print(f"  Total agents: {len(manifest['agent_slug_list'])}")

    # -----------------------------------------------------------------------
    # Step 4: Resolve output directory
    # -----------------------------------------------------------------------
    if args.output:
        output_dir = Path(args.output).resolve()
    elif description.get("existing_project_path"):
        project_path = Path(description["existing_project_path"])
        output_dir = adapter.get_agents_dir(project_path)
    else:
        output_dir = Path.cwd() / ".github" / "agents"

    print(f"  Output directory: {output_dir}")

    # -----------------------------------------------------------------------
    # Step 5: Render
    # -----------------------------------------------------------------------
    print("Rendering templates...")
    rendered = render.render_all(manifest, templates_dir=TEMPLATES_DIR)

    # Apply framework-specific post-processing
    final_rendered: list[tuple[str, str]] = []
    for rel_path, content in rendered:
        file_type = _guess_file_type(rel_path)
        if file_type == "agent":
            slug = Path(rel_path).stem.replace(".agent", "")
            content = adapter.render_agent_file(content, slug, manifest)
        elif file_type == "instructions":
            content = adapter.render_instructions_file(content, manifest)
        final_rendered.append((rel_path, content))

    # -----------------------------------------------------------------------
    # Step 6: Validate cross-references
    # -----------------------------------------------------------------------
    warnings = render.validate_cross_refs(final_rendered)
    if warnings:
        manifest["_cross_ref_warnings"] = warnings
        print(f"  ⚠  {len(warnings)} cross-reference warning(s)")

    # -----------------------------------------------------------------------
    # Step 7: Emit
    # -----------------------------------------------------------------------
    result = emit.emit_all(
        final_rendered,
        output_dir=output_dir,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        yes=args.yes,
    )

    # -----------------------------------------------------------------------
    # Step 8: Print summary
    # -----------------------------------------------------------------------
    emit.print_summary(result, manifest)

    # -----------------------------------------------------------------------
    # Step 9: Write run log (skip in dry-run)
    # -----------------------------------------------------------------------
    if not args.dry_run and result.success:
        _write_run_log(manifest, result, output_dir)

    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guess_file_type(rel_path: str) -> str:
    if "copilot-instructions" in rel_path:
        return "instructions"
    if "SETUP-REQUIRED" in rel_path:
        return "setup-required"
    if "team-builder" in rel_path:
        return "builder"
    return "agent"


def _write_run_log(manifest: dict, result: emit.EmitResult, output_dir: Path) -> None:
    """Write a minimal JSON run log to the output directory."""
    log = {
        "schema_version": "1.0",
        "project_name": manifest["project_name"],
        "framework": manifest["framework"],
        "project_type": manifest["project_type"],
        "archetypes": manifest["selected_archetypes"],
        "components": [c["slug"] for c in manifest["components"]],
        "files_written": result.written,
        "manual_required": len(manifest.get("manual_required_placeholders", [])),
    }
    log_path = output_dir / "references" / "build-log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
