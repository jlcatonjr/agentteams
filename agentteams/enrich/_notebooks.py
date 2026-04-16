"""_notebooks.py — Jupyter notebook section extraction and component fill building."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._models import _HEADING_LINE_RE


def _extract_notebook_sections(notebook_path: Path) -> list[str]:
    """Return ## and # section headings from a Jupyter notebook.

    Args:
        notebook_path: Path to a .ipynb file.

    Returns:
        List of heading strings (stripped of leading #s), in order.
    """
    try:
        nb = json.loads(notebook_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    headings: list[str] = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "markdown":
            continue
        src = "".join(cell.get("source", []))
        for line in src.splitlines():
            m = _HEADING_LINE_RE.match(line)
            if m:
                headings.append(line.lstrip("#").strip())
    return headings


def _build_component_fills(
    component: dict[str, Any],
    project_path: Path | None,
    manifest: dict[str, Any],
) -> dict[str, str]:
    """Build project-aware fill values for a single component's MANUAL tokens.

    Args:
        component:    The component dict from manifest['components'].
        project_path: Root of the project repo (or None).
        manifest:     Full team manifest.

    Returns:
        Dict mapping MANUAL token names → fill values.
    """
    name = component.get("name", component.get("slug", ""))
    slug = component.get("slug", "")
    number = component.get("number", "")
    output_fmt = manifest.get("output_format", "Jupyter notebooks (.ipynb)")

    fills: dict[str, str] = {}
    matched_nb_path: Path | None = None

    sections: list[str] = []
    if project_path:
        primary_dir = manifest.get("primary_output_dir", "Textbook/")
        nb_dir = project_path / primary_dir.rstrip("/")
        # Exclude .ipynb_checkpoints from candidates
        candidates = [
            p for p in sorted(nb_dir.glob("**/*.ipynb"))
            if ".ipynb_checkpoints" not in p.parts
        ]
        for nb_path in candidates:
            nb_name = nb_path.name.lower()
            if f"chapter {number}" in nb_name or f"chapter{number}" in nb_name:
                sections = _extract_notebook_sections(nb_path)
                matched_nb_path = nb_path
                break
            slug_normalized = slug.lower().replace("-", " ").replace("ch", "chapter ")
            slug_parts = [p for p in slug_normalized.split() if p not in ("ch",)]
            if slug_parts and all(p in nb_name for p in slug_parts[:2]):
                sections = _extract_notebook_sections(nb_path)
                matched_nb_path = nb_path
                break

    # --- COMPONENT_SPEC ---
    if sections:
        main_sections = [s for s in sections if s and s != sections[0]][:6]
        spec_lines = [f"{name} teaches the following core concepts:", ""] + [
            f"- {s}" for s in main_sections
        ]
        fills["COMPONENT_SPEC"] = "\n".join(spec_lines)
    else:
        fills["COMPONENT_SPEC"] = (
            f"{name} — component {number} of the {manifest.get('project_name', 'project')}. "
            f"See the source notebook for detailed content specification."
        )

    # --- COMPONENT_SECTIONS ---
    if sections:
        section_lines = [f"{i + 1}. **{s}**" for i, s in enumerate(sections[:10]) if s]
        fills["COMPONENT_SECTIONS"] = (
            "\n".join(section_lines) if section_lines else f"See source notebook: {name}"
        )
    else:
        fills["COMPONENT_SECTIONS"] = f"See source notebook for {name} sections."

    # --- COMPONENT_QUALITY_CRITERIA ---
    fills["COMPONENT_QUALITY_CRITERIA"] = (
        f"- All code cells execute without errors in a clean kernel restart\n"
        f"- Each section opens with a clear learning objective or conceptual framing\n"
        f"- Code is annotated with inline comments explaining non-obvious steps\n"
        f"- Examples use economics, statistics, or social-science data where applicable\n"
        f"- Output format is a clean, readable {output_fmt} file"
    )

    # --- COMPONENT_SOURCES ---
    primary_dir = manifest.get("primary_output_dir", "Textbook/").rstrip("/")
    if matched_nb_path is not None:
        fills["COMPONENT_SOURCES"] = f"- {primary_dir}/{matched_nb_path.name}"
    else:
        fills["COMPONENT_SOURCES"] = f"- {primary_dir}/ (see chapter {number} notebook)"

    return fills
