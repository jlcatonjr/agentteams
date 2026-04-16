"""_notebooks.py — Jupyter notebook section extraction and component fill building."""

from __future__ import annotations

import json
import re
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


def _extract_notebook_imports(notebook_path: Path) -> set[str]:
    """Return top-level package names imported in code cells of a notebook.

    Args:
        notebook_path: Path to a .ipynb file.

    Returns:
        Set of package/module names found in import statements.
    """
    try:
        nb = json.loads(notebook_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()

    _import_re = re.compile(
        r"^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import)", re.MULTILINE
    )
    found: set[str] = set()
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        src = "".join(cell.get("source", []))
        for m in _import_re.finditer(src):
            pkg = (m.group(1) or m.group(2) or "").split(".")[0]
            if pkg:
                found.add(pkg)
    return found


def _name_similarity(component_name: str, notebook_name: str) -> float:
    """Score how well a notebook filename matches a component name (0.0–1.0).

    Uses token overlap: fraction of component name words present in notebook name.
    Applies a bonus for chapter-number matches and penalises very short overlap.

    Args:
        component_name: Human-readable component name (e.g. "Chapter 9 - IS-LM").
        notebook_name:  Notebook filename without extension, lowercased.

    Returns:
        Float similarity score — higher is better.
    """
    comp_lower = component_name.lower()
    nb_lower = notebook_name.lower()

    # Tokenise: split on spaces, hyphens, underscores; drop short stopwords
    _stopwords = {"the", "a", "an", "of", "and", "or", "in", "for", "to", "with"}
    comp_tokens = [
        t for t in re.split(r"[\s\-_]+", comp_lower) if len(t) > 1 and t not in _stopwords
    ]
    if not comp_tokens:
        return 0.0

    matches = sum(1 for t in comp_tokens if t in nb_lower)
    score = matches / len(comp_tokens)

    # Bonus: exact chapter-number hit ("chapter 9", "ch9", etc.)
    chap_m = re.search(r"\bch(?:apter\s*)?(\d+)\b", comp_lower)
    if chap_m:
        num = chap_m.group(1)
        if re.search(rf"\bch(?:apter\s*)?0*{num}\b", nb_lower):
            score += 0.5

    return score


def _best_notebook_match(
    component: dict[str, Any],
    candidates: list[Path],
) -> Path | None:
    """Return the best-matching notebook for a component using similarity scoring.

    Args:
        component:  The component dict (needs 'name', 'slug', 'number').
        candidates: Sorted list of .ipynb paths to search.

    Returns:
        Best-matching Path, or None if no candidate scores above the minimum threshold.
    """
    name = component.get("name", "")
    slug = component.get("slug", "")
    number = str(component.get("number", ""))

    _MIN_SCORE = 0.35  # minimum to be considered a match

    best_path: Path | None = None
    best_score = 0.0

    for nb_path in candidates:
        nb_stem = nb_path.stem  # filename without .ipynb
        score = _name_similarity(name, nb_stem)

        # Also score against slug (as backup signal)
        slug_score = _name_similarity(slug.replace("-", " "), nb_stem)
        score = max(score, slug_score * 0.8)  # weight slug slightly lower

        if score > best_score:
            best_score = score
            best_path = nb_path

    return best_path if best_score >= _MIN_SCORE else None


def _build_component_fills(
    component: dict[str, Any],
    project_path: Path | None,
    manifest: dict[str, Any],
) -> dict[str, str]:
    """Build project-aware fill values for a single component's MANUAL tokens.

    Improvements over naive approach:
    - Edit-distance-based notebook matching (best scored match, not first greedy match)
    - Directory-based deliverables: returns directory path when deliverable is a folder
    - Per-component tool dependencies: scanned from matched notebook's import cells
    - Auto-inferred cross-references: prev/next component ordering from manifest

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
    deliverable = component.get("deliverable", "")

    fills: dict[str, str] = {}
    matched_nb_path: Path | None = None
    matched_is_dir = False

    sections: list[str] = []
    notebook_imports: set[str] = set()

    if project_path:
        primary_dir = manifest.get("primary_output_dir", "Textbook/")
        nb_dir = project_path / primary_dir.rstrip("/")

        # --- Improvement #3: directory-based deliverables ---
        # If the component's deliverable field points to a directory, record it directly.
        if deliverable and not deliverable.endswith(".ipynb"):
            deliverable_path = project_path / deliverable
            if deliverable_path.is_dir():
                matched_is_dir = True
                # Try to find a representative "guide" or "index" notebook in that dir
                dir_nbs = [
                    p for p in sorted(deliverable_path.glob("*.ipynb"))
                    if ".ipynb_checkpoints" not in p.parts
                ]
                guide_nbs = [
                    p for p in dir_nbs
                    if any(kw in p.stem.lower() for kw in ("guide", "index", "intro", "overview", "readme"))
                ]
                rep_nb = guide_nbs[0] if guide_nbs else (dir_nbs[0] if dir_nbs else None)
                if rep_nb:
                    sections = _extract_notebook_sections(rep_nb)
                    notebook_imports = _extract_notebook_imports(rep_nb)
                    matched_nb_path = rep_nb
                # COMPONENT_OUTPUT_FILE points to the directory itself
                fills["COMPONENT_OUTPUT_FILE"] = deliverable.rstrip("/")
                fills["COMPONENT_SOURCES"] = f"- {deliverable.rstrip('/')}/"

        # --- Improvement #4: edit-distance notebook matching for single-file deliverables ---
        if not matched_is_dir:
            candidates = [
                p for p in sorted(nb_dir.glob("**/*.ipynb"))
                if ".ipynb_checkpoints" not in p.parts
            ]
            matched_nb_path = _best_notebook_match(component, candidates)
            if matched_nb_path:
                sections = _extract_notebook_sections(matched_nb_path)
                notebook_imports = _extract_notebook_imports(matched_nb_path)

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

    # --- COMPONENT_SOURCES / COMPONENT_OUTPUT_FILE (single-file case) ---
    primary_dir = manifest.get("primary_output_dir", "Textbook/").rstrip("/")
    if "COMPONENT_OUTPUT_FILE" not in fills:
        if matched_nb_path is not None:
            rel = f"{primary_dir}/{matched_nb_path.name}"
            fills["COMPONENT_SOURCES"] = f"- {rel}"
            fills["COMPONENT_OUTPUT_FILE"] = rel
        else:
            fills["COMPONENT_SOURCES"] = f"- {primary_dir}/ (see chapter {number} notebook)"

    # --- Improvement #1: auto-infer COMPONENT_CROSS_REFS from component ordering ---
    all_components = manifest.get("components", [])
    comp_index = next(
        (i for i, c in enumerate(all_components) if c.get("slug") == slug), None
    )
    cross_ref_lines: list[str] = []
    if comp_index is not None:
        if comp_index > 0:
            prev = all_components[comp_index - 1]
            cross_ref_lines.append(
                f"- Builds on `{prev['slug']}` — {prev.get('name', prev['slug'])}"
            )
        if comp_index < len(all_components) - 1:
            nxt = all_components[comp_index + 1]
            cross_ref_lines.append(
                f"- Leads to `{nxt['slug']}` — {nxt.get('name', nxt['slug'])}"
            )
    fills["COMPONENT_CROSS_REFS"] = (
        "\n".join(cross_ref_lines) if cross_ref_lines else "None specified."
    )

    # --- Improvement #2: per-component tool dependencies from notebook imports ---
    if notebook_imports:
        # Normalise imports to package names using the tool catalog alias map
        try:
            from ._tools import _IMPORT_TO_PACKAGE, _CANONICAL_DOCS, _TOOL_CATALOG
        except ImportError:
            _IMPORT_TO_PACKAGE = {}
            _CANONICAL_DOCS = {}
            _TOOL_CATALOG = {}

        tool_agent_names = {
            ta.get("tool_name", "").lower(): ta["slug"]
            for ta in manifest.get("tool_agents", [])
        }
        ref_tool_names = {
            rt.get("tool_name", "").lower(): rt["slug"]
            for rt in manifest.get("reference_tools", [])
        }

        stdlib = {
            "os", "sys", "re", "json", "math", "collections", "itertools",
            "functools", "pathlib", "typing", "abc", "io", "csv", "datetime",
            "time", "random", "copy", "warnings", "logging", "unittest",
            "argparse", "shutil", "tempfile", "subprocess", "threading",
            "multiprocessing", "contextlib", "dataclasses", "enum", "string",
            "textwrap", "pprint", "struct", "array", "queue", "heapq",
            "bisect", "operator", "inspect", "importlib", "pkgutil",
            "__future__", "builtins", "types", "weakref", "gc",
        }

        tool_lines: list[str] = []
        seen: set[str] = set()
        for raw_import in sorted(notebook_imports):
            if raw_import in stdlib:
                continue
            pkg = _IMPORT_TO_PACKAGE.get(raw_import, raw_import)
            pkg_lower = pkg.lower()
            if pkg_lower in seen:
                continue
            seen.add(pkg_lower)

            if pkg_lower in tool_agent_names:
                tool_lines.append(f"- `@{tool_agent_names[pkg_lower]}` (specialist agent)")
            elif pkg_lower in ref_tool_names:
                tool_lines.append(
                    f"- `references/{ref_tool_names[pkg_lower]}-reference.md`"
                )
            elif pkg_lower in _CANONICAL_DOCS or pkg_lower in _TOOL_CATALOG:
                tool_lines.append(f"- {pkg}")
            else:
                # Include it if it looks like a real third-party package
                tool_lines.append(f"- {pkg}")

        fills["COMPONENT_TOOLS"] = (
            "\n".join(tool_lines) if tool_lines else "No tool-specific dependencies."
        )
    else:
        fills["COMPONENT_TOOLS"] = "No tool-specific dependencies."

    return fills
