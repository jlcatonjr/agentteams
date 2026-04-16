"""_audit.py — Underdevelopment detection and the main scan_defaults function."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ._fills import _RULE_BASED_FILLS
from ._models import DefaultFinding, _MANUAL_RE, _SECTION_RE
from ._tools import _CANONICAL_DOCS, _IMPORT_TO_PACKAGE, _get_docs_url, scan_project_imports


#: Generic boilerplate phrases — a section body containing *only* these is underdeveloped.
_GENERIC_PHRASES: frozenset[str] = frozenset({
    "to be defined", "tbd", "todo", "fill in", "fill me in",
    "add content here", "add details here",
    "no formal style guide", "no citation database",
    "see source", "see notebook",
})


def _section_is_underdeveloped(body_lines: list[str]) -> bool:
    """Return True if a section's body is empty, comment-only, or all-boilerplate."""
    non_empty = [ln.strip() for ln in body_lines if ln.strip()]
    if not non_empty:
        return True
    if any(ln.startswith("#") for ln in non_empty):
        return False
    if len(non_empty) > 4:
        return False
    if all(re.match(r"<!--.*-->", ln, re.DOTALL) for ln in non_empty):
        return True
    if all(any(p in ln.lower() for p in _GENERIC_PHRASES) for ln in non_empty):
        return True
    return False


def scan_defaults(
    file_map: dict[str, str],
    manifest: dict[str, Any],
    project_path: Path | None = None,
) -> list[DefaultFinding]:
    """Scan generated agent files for unresolved default template elements.

    Detects:
    - ``{MANUAL:TOKEN}`` placeholders remaining in agent files.
    - Tool reference files with incomplete metadata.
    - Underdeveloped sections (comment-only bodies, generic boilerplate).
    - Packages imported in project source that have no reference file.

    Args:
        file_map:     Rendered file content keyed by relative path.
        manifest:     Team manifest from analyze.build_manifest().
        project_path: Project root — enables import scanning for coverage gaps.

    Returns:
        List of DefaultFinding, one per finding.
    """
    findings: list[DefaultFinding] = []

    detected_packages: dict[str, str] = {}
    if project_path and project_path.exists():
        detected_packages = scan_project_imports(project_path)

    comp_by_slug = {c["slug"]: c for c in manifest.get("components", [])}

    for rel_path, content in file_map.items():
        if rel_path.endswith("SETUP-REQUIRED.md"):
            continue
        if "content-enricher" in rel_path:
            continue

        lines = content.splitlines()
        current_section = ""

        section_at_line: list[str] = []
        for line in lines:
            m = _SECTION_RE.match(line)
            if m:
                current_section = m.group(1).strip()
            section_at_line.append(current_section)

        seen_in_file: set[str] = set()
        for i, line in enumerate(lines):
            for m in _MANUAL_RE.finditer(line):
                token = m.group(1)
                key = (rel_path, token)
                if key in seen_in_file:
                    continue
                seen_in_file.add(key)

                category = "TOOL_METADATA" if "references/" in rel_path else "MANUAL_PLACEHOLDER"

                ctx_lines = lines[max(0, i - 1): i + 2]
                ctx = " | ".join(ln.strip() for ln in ctx_lines if ln.strip())[:120]

                suggestion = ""
                if token in _RULE_BASED_FILLS:
                    rule = _RULE_BASED_FILLS[token]
                    if callable(rule):
                        suggestion = rule(manifest)
                    else:
                        suggestion = str(rule)

                if not suggestion and category == "MANUAL_PLACEHOLDER":
                    agent_slug = Path(rel_path).stem.replace(".agent", "")
                    if agent_slug.endswith("-expert"):
                        comp_slug = agent_slug[: -len("-expert")]
                        comp = comp_by_slug.get(comp_slug)
                        if comp and token in (
                            "COMPONENT_SPEC", "COMPONENT_SECTIONS", "COMPONENT_QUALITY_CRITERIA"
                        ):
                            suggestion = (
                                f"[auto-fill from source notebook for {comp.get('name', comp_slug)}]"
                            )

                findings.append(DefaultFinding(
                    file=rel_path,
                    category=category,
                    token=token,
                    line_no=i + 1,
                    section=section_at_line[i],
                    context_snippet=ctx,
                    auto_suggestion=suggestion,
                    status="pending",
                ))

        _skip_sections = frozenset({
            "invariant core", "cross-references", "verdict format",
            "output format", "cross-reference map", "review protocol",
            "component brief preparation", "related agents", "scope limits",
            "enrichment workflow", "token reference", "tool dependencies",
        })
        cur_sec: str = ""
        cur_start: int = 0
        cur_body: list[str] = []
        cur_depth: int = 0

        def _flush(sec: str, start: int, body: list[str]) -> None:
            if not sec or sec.lower() in _skip_sections:
                return
            if _section_is_underdeveloped(body):
                ctx = " | ".join(ln.strip() for ln in body[:3] if ln.strip())[:120]
                findings.append(DefaultFinding(
                    file=rel_path,
                    category="GENERIC_SECTION",
                    token=sec,
                    line_no=start + 1,
                    section=sec,
                    context_snippet=ctx,
                    auto_suggestion="",
                    status="pending",
                ))

        for i, line in enumerate(lines):
            m = _SECTION_RE.match(line)
            if m:
                depth = len(line) - len(line.lstrip("#"))
                if depth == 1:
                    _flush(cur_sec, cur_start, cur_body)
                    cur_sec = ""
                    cur_start = i
                    cur_body = []
                    cur_depth = 1
                elif depth <= cur_depth or cur_depth == 0:
                    _flush(cur_sec, cur_start, cur_body)
                    cur_sec = m.group(1).strip()
                    cur_start = i
                    cur_body = []
                    cur_depth = depth
                else:
                    cur_body.append(line)
            else:
                cur_body.append(line)
        _flush(cur_sec, cur_start, cur_body)

    # --- Check for imported packages with no reference file ---
    brief_tools: set[str] = set()
    for t in manifest.get("tools", []):
        name = (t.get("name", "") if isinstance(t, dict) else str(t)).lower().replace("_", "-")
        pkg = _IMPORT_TO_PACKAGE.get(name, name)
        brief_tools.add(pkg)

    ref_file_stems = {
        Path(p).stem.lower().replace("-", "").replace("_", "").replace("reference", "").replace("ref", "")
        for p in file_map if "references/" in p and p.endswith(".md")
    }
    for pkg_name, alias in detected_packages.items():
        has_canonical = pkg_name in _CANONICAL_DOCS or pkg_name in brief_tools
        if not has_canonical:
            continue
        pkg_norm = pkg_name.lower().replace("-", "").replace("_", "")
        has_ref = any(pkg_norm in stem for stem in ref_file_stems)
        if not has_ref:
            findings.append(DefaultFinding(
                file="references/",
                category="MISSING_TOOL_REF",
                token=pkg_name,
                line_no=0,
                section="",
                context_snippet=(
                    f"import alias '{alias}' detected in project source; no reference file found."
                ),
                auto_suggestion=_get_docs_url(pkg_name),
                status="pending",
            ))

    return findings
