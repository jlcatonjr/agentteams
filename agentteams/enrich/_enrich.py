"""_enrich.py — Enrichment transforms, CSV I/O, and AI enrichment."""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ._fills import _RULE_BASED_FILLS
from ._models import DefaultFinding
from ._notebooks import _build_component_fills
from ._tools import (
    _IMPORT_TO_PACKAGE,
    _get_docs_url,
    build_tool_catalog,
    scan_project_imports,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_tool_names_from_files(file_map: dict[str, str]) -> dict[str, str]:
    """Map relative paths of tool reference files to their tool name."""
    _tool_name_re = re.compile(r"^# Tool Reference: (.+)", re.MULTILINE)
    _tool_name_re2 = re.compile(r"Tool Name.*?:\s*(.+)", re.IGNORECASE)
    result: dict[str, str] = {}
    for rel_path, content in file_map.items():
        if "references/" not in rel_path or not rel_path.endswith(".md"):
            continue
        m = _tool_name_re.search(content) or _tool_name_re2.search(content)
        if m:
            result[rel_path] = m.group(1).strip().lower()
    return result


def _resolve_tool_key(rel_path: str, tool_name_by_file: dict[str, str]) -> str | None:
    """Return canonical package name for a tool reference file path, or None."""
    raw = tool_name_by_file.get(rel_path, "").lower().strip()
    if raw:
        return _IMPORT_TO_PACKAGE.get(raw, raw)
    stem = Path(rel_path).stem
    normalized = stem.replace("ref-", "").replace("-reference", "").replace("-ref", "")
    if normalized:
        return _IMPORT_TO_PACKAGE.get(normalized, normalized)
    return None


def _build_project_context(manifest: dict[str, Any], project_path: Path | None) -> str:
    """Build a compact project context string for AI prompts."""
    lines = [
        f"Project: {manifest.get('project_name')}",
        f"Goal: {manifest.get('project_goal', '')}",
        f"Output format: {manifest.get('output_format', '')}",
        f"Primary output dir: {manifest.get('primary_output_dir', '')}",
        f"Components: {[c['name'] for c in manifest.get('components', [])]}",
        f"Tools: {[t.get('name', t) if isinstance(t, dict) else t for t in manifest.get('tools', [])]}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API — auto_enrich
# ---------------------------------------------------------------------------

def auto_enrich(
    findings: list[DefaultFinding],
    file_map: dict[str, str],
    manifest: dict[str, Any],
    project_path: Path | None = None,
) -> tuple[dict[str, str], list[DefaultFinding]]:
    """Apply all resolvable fills to file_map, returning an enriched copy.

    Args:
        findings:     Findings from scan_defaults().
        file_map:     Rendered file content keyed by relative path.
        manifest:     Team manifest from analyze.build_manifest().
        project_path: Absolute path to the project repo root (enables notebook scanning).

    Returns:
        Tuple of (enriched_file_map, updated_findings).
    """
    enriched = dict(file_map)

    comp_by_slug = {c["slug"]: c for c in manifest.get("components", [])}
    comp_fills_cache: dict[str, dict[str, str]] = {}

    tool_name_by_file: dict[str, str] = _extract_tool_names_from_files(file_map)

    detected_pkgs: dict[str, str] = {}
    if project_path and project_path.exists():
        detected_pkgs = scan_project_imports(project_path)
    listed_tools = [
        (t.get("name", "") if isinstance(t, dict) else str(t)).lower()
        for t in manifest.get("tools", [])
    ]
    all_pkgs = list(
        {_IMPORT_TO_PACKAGE.get(n, n) for n in list(detected_pkgs.keys()) + listed_tools if n}
    )
    _dynamic_catalog = build_tool_catalog(all_pkgs, fetch_pypi=True)

    findings_by_file: dict[str, list[DefaultFinding]] = {}
    for f in findings:
        findings_by_file.setdefault(f.file, []).append(f)

    for rel_path, file_findings in findings_by_file.items():
        if rel_path not in enriched:
            continue
        content = enriched[rel_path]
        modified = False

        for finding in file_findings:
            token = finding.token
            replacement: str | None = None

            if token in _RULE_BASED_FILLS:
                rule = _RULE_BASED_FILLS[token]
                value = rule(manifest) if callable(rule) else str(rule)
                if value:
                    replacement = value

            if token in ("TOOL_DOCS_URL", "TOOL_API_SURFACE", "TOOL_COMMON_PATTERNS"):
                tool_key = _resolve_tool_key(rel_path, tool_name_by_file)
                if tool_key:
                    catalog_entry = _dynamic_catalog.get(tool_key, {})
                    field_map = {
                        "TOOL_DOCS_URL": "docs_url",
                        "TOOL_API_SURFACE": "api_surface",
                        "TOOL_COMMON_PATTERNS": "common_patterns",
                    }
                    candidate = catalog_entry.get(field_map[token], "")
                    if not candidate and token == "TOOL_DOCS_URL":
                        candidate = _get_docs_url(tool_key)
                    if candidate:
                        replacement = candidate

            if token in (
                "COMPONENT_SPEC", "COMPONENT_SECTIONS",
                "COMPONENT_QUALITY_CRITERIA", "COMPONENT_SOURCES",
            ):
                agent_slug = Path(rel_path).stem.replace(".agent", "")
                if agent_slug.endswith("-expert"):
                    comp_slug = agent_slug[: -len("-expert")]
                    if comp_slug not in comp_fills_cache:
                        comp = comp_by_slug.get(comp_slug)
                        if comp:
                            comp_fills_cache[comp_slug] = _build_component_fills(
                                comp, project_path, manifest
                            )
                    comp_fills = comp_fills_cache.get(comp_slug, {})
                    replacement = comp_fills.get(token, "")

            if replacement:
                placeholder = "{MANUAL:" + token + "}"
                if placeholder in content:
                    content = content.replace(placeholder, replacement)
                    finding.auto_suggestion = replacement
                    finding.status = "auto_filled"
                    modified = True

        if modified:
            _comment_re = re.compile(r"\n<!-- .+? -->\n", re.DOTALL)
            content = _comment_re.sub("\n", content)
            enriched[rel_path] = content

    # --- Generate stub reference files for MISSING_TOOL_REF findings ---
    missing_ref_findings = [
        f for f in findings if f.category == "MISSING_TOOL_REF" and f.status == "pending"
    ]
    for finding in missing_ref_findings:
        pkg = finding.token
        docs_url = finding.auto_suggestion or _get_docs_url(pkg)
        catalog = _dynamic_catalog.get(pkg, {})
        api_surface = catalog.get("api_surface", "")
        common_patterns = catalog.get("common_patterns", "")
        project_name = manifest.get("project_name", "Project")
        display_name = pkg.replace("-", " ").title()

        stub = (
            f"# {display_name} Reference — {project_name}\n\n"
            f"> Quick-reference for **{display_name}** in {project_name}.\n"
            f"> This is a lightweight reference file, not a full agent.\n\n"
            f"---\n\n"
            f"## Official Documentation\n\n"
            f"{docs_url if docs_url else '{MANUAL:TOOL_DOCS_URL}'}\n\n"
            f"## Key API Surface\n\n"
            f"{api_surface if api_surface else '{MANUAL:TOOL_API_SURFACE}'}\n\n"
            f"## Common Patterns & Pitfalls\n\n"
            f"{common_patterns if common_patterns else '{MANUAL:TOOL_COMMON_PATTERNS}'}\n\n"
            f"## Key Conventions\n\n"
            f"- Follow project style rules when using {display_name}\n"
            f"- Refer to authority sources for API contract accuracy\n\n"
            f"## Related Agents\n\n"
            f"- `@technical-validator` — verify technical accuracy of {display_name} usage\n"
            f"- `@primary-producer` — implements code that depends on {display_name}\n"
        )
        ref_rel_path = f"references/ref-{pkg.lower().replace(' ', '-')}-reference.md"
        if ref_rel_path not in enriched:
            enriched[ref_rel_path] = stub
            finding.auto_suggestion = docs_url
            finding.status = "auto_filled"

    return enriched, findings


# ---------------------------------------------------------------------------
# Public API — CSV export / import
# ---------------------------------------------------------------------------

_CSV_FIELDS = [
    "file", "category", "token", "line_no", "section",
    "context_snippet", "auto_suggestion", "status",
]


def export_csv(findings: list[DefaultFinding], csv_path: Path) -> None:
    """Write DefaultFindings to a CSV file.

    Args:
        findings: List of DefaultFinding items from scan_defaults().
        csv_path: Absolute path of the output CSV file. Parent directory must exist.
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for f in findings:
            writer.writerow({
                "file": f.file,
                "category": f.category,
                "token": f.token,
                "line_no": f.line_no,
                "section": f.section,
                "context_snippet": f.context_snippet,
                "auto_suggestion": f.auto_suggestion,
                "status": f.status,
            })


def load_csv(csv_path: Path) -> list[DefaultFinding]:
    """Load DefaultFindings from a previously exported CSV file.

    Args:
        csv_path: Absolute path to the CSV file produced by export_csv().

    Returns:
        List of DefaultFinding items.
    """
    findings: list[DefaultFinding] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            findings.append(DefaultFinding(
                file=row["file"],
                category=row["category"],
                token=row["token"],
                line_no=int(row.get("line_no", 0)),
                section=row.get("section", ""),
                context_snippet=row.get("context_snippet", ""),
                auto_suggestion=row.get("auto_suggestion", ""),
                status=row.get("status", "pending"),
            ))
    return findings


def print_enrich_summary(
    findings: list[DefaultFinding],
    *,
    verbose: bool = False,
) -> None:
    """Print a human-readable enrichment summary to stdout.

    Args:
        findings: List of findings (after auto_enrich has set statuses).
        verbose:  If True, list all pending findings individually.
    """
    auto_filled = [f for f in findings if f.status == "auto_filled"]
    ai_filled = [f for f in findings if f.status == "ai_filled"]
    pending = [f for f in findings if f.status == "pending"]

    print(f"\n  --- Defaults Audit & Enrichment ---")
    print(f"  Total default findings:  {len(findings)}")
    print(f"  Auto-filled:             {len(auto_filled)}")
    if ai_filled:
        print(f"  AI-filled:               {len(ai_filled)}")
    print(f"  Still pending:           {len(pending)}")

    if pending:
        if verbose:
            print("\n  Pending findings (require manual completion or --post-audit AI fill):")
            for f in pending:
                print(f"    {f.file}:{f.line_no}  [{f.token}]  ({f.section})")
        else:
            print(f"  Review defaults-audit.csv in the references/ directory for details.")


# ---------------------------------------------------------------------------
# AI enrichment (optional — requires standalone `copilot` CLI on PATH)
# ---------------------------------------------------------------------------

def ai_enrich(
    findings: list[DefaultFinding],
    file_map: dict[str, str],
    manifest: dict[str, Any],
    *,
    project_path: Path | None = None,
    copilot_path: str | None = None,
) -> tuple[dict[str, str], list[DefaultFinding]]:
    """Use the standalone copilot CLI to fill in pending default findings.

    Args:
        findings:     DefaultFinding list (modified in-place with updated statuses).
        file_map:     Enriched file content keyed by relative path.
        manifest:     Team manifest.
        project_path: Project repo root for context.
        copilot_path: Path to the copilot binary (auto-detected if None).

    Returns:
        Tuple of (further_enriched_file_map, updated_findings).
    """
    if copilot_path is None:
        copilot_path = shutil.which("copilot")
    if not copilot_path:
        return file_map, findings

    pending = [f for f in findings if f.status == "pending"]
    if not pending:
        return file_map, findings

    enriched = dict(file_map)

    files_with_pending: set[str] = {f.file for f in pending}
    for rel_path in sorted(files_with_pending):
        file_pending = [f for f in pending if f.file == rel_path]
        if not file_pending:
            continue

        content = enriched.get(rel_path, "")
        project_context = _build_project_context(manifest, project_path)

        token_list = "\n".join(
            f"- {{MANUAL:{fp.token}}} (section: {fp.section or 'top-level'})"
            for fp in file_pending
        )

        prompt = (
            f"You are enriching an AI agent file for project "
            f"'{manifest.get('project_name', 'unknown')}'. "
            f"The project goal: {manifest.get('project_goal', '')} "
            f"Replace each {{MANUAL:TOKEN}} placeholder with a concise, accurate value "
            f"appropriate for this project. Do not invent facts; use only the context provided. "
            f"Return ONLY the complete updated file content — no explanation, no markdown fence.\n\n"
            f"Project context:\n{project_context}\n\n"
            f"Tokens to fill:\n{token_list}\n\n"
            f"File content:\n{content}"
        )

        try:
            proc = subprocess.run(
                [copilot_path, "-p", prompt, "--no-ask-user",
                 "--no-custom-instructions", "--model", "gpt-4o", "--silent"],
                capture_output=True, text=True, timeout=120,
            )
            output = proc.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue

        if not output:
            continue

        before_count = len(re.findall(r"\{MANUAL:", content))
        after_count = len(re.findall(r"\{MANUAL:", output))
        if after_count < before_count:
            enriched[rel_path] = output
            for fp in file_pending:
                if "{MANUAL:" + fp.token + "}" not in output:
                    fp.status = "ai_filled"

    return enriched, findings
