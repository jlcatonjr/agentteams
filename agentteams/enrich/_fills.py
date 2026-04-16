"""_fills.py — Rule-based fill functions for known MANUAL token patterns."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _fill_reference_db_path(manifest: dict[str, Any]) -> str:
    """Return 'N/A' when no reference database is configured."""
    desc = manifest.get("description", {}) or {}
    if desc.get("reference_db") or desc.get("reference_db_path"):
        return ""  # has a real value — don't overwrite
    return "N/A — no citation database configured for this project"


def _fill_style_reference_path(manifest: dict[str, Any]) -> str:
    """Return 'N/A' when no style guide is configured."""
    desc = manifest.get("description", {}) or {}
    if desc.get("style_reference") or desc.get("style_reference_path"):
        return str(desc.get("style_reference") or desc.get("style_reference_path"))
    if manifest.get("style_reference"):
        return str(manifest["style_reference"])
    return "N/A — no formal style guide defined for this project"


def _fill_conversion_pipeline(manifest: dict[str, Any]) -> str:
    """Return 'N/A' when source format == output format."""
    output_fmt = (manifest.get("output_format") or "").lower()
    if "ipynb" in output_fmt or "notebook" in output_fmt:
        return (
            "N/A — source Jupyter notebooks are the final deliverable format; "
            "no format conversion step is required"
        )
    if "md" in output_fmt or "markdown" in output_fmt:
        return (
            "N/A — Markdown files are the final deliverable format; "
            "no format conversion step is required"
        )
    return ""  # non-trivial conversion pipeline — leave for human


def _fill_conflict_log_path(_manifest: dict[str, Any]) -> str:
    """Return the standard conflict log path."""
    return ".github/agents/references/conflict-log.csv"


def _fill_figures_dir(manifest: dict[str, Any]) -> str:
    """Return the figures directory path."""
    primary = manifest.get("primary_output_dir") or "docs/"
    return str(Path(primary) / "figures")


def _fill_build_output_dir(manifest: dict[str, Any]) -> str:
    """Return the build output directory."""
    return manifest.get("primary_output_dir") or "dist/"


#: Keys are MANUAL token names. Values are callables (or strings) that produce
#: the replacement given the project manifest/description.
_RULE_BASED_FILLS: dict[str, Any] = {
    "REFERENCE_DB_PATH": _fill_reference_db_path,
    "STYLE_REFERENCE_PATH": _fill_style_reference_path,
    "CONVERSION_PIPELINE": _fill_conversion_pipeline,
    "CONFLICT_LOG_PATH": _fill_conflict_log_path,
    "FIGURES_DIR": _fill_figures_dir,
    "BUILD_OUTPUT_DIR": _fill_build_output_dir,
    "DIAGRAM_TOOLS": "draw.io / Mermaid / mermaid-js (Markdown-native), or Graphviz for programmatic graphs",
    "DIAGRAM_EXTENSION": "drawio",
    "COMPONENT_SOURCES": lambda m: (
        f"- {m.get('primary_output_dir', 'src/')} (project source)"
        if m.get("primary_output_dir") else "- Refer to project source files"
    ),
}
