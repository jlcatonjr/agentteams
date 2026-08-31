"""enrich — Default-value audit and context-aware enrichment for generated agent teams.

After build, scans agent files for unresolved {MANUAL:*} placeholders and
underdeveloped template sections, exports an audit CSV, then attempts
auto-enrichment using:

  1. Rule-based fills — known-pattern tokens resolvable from the project brief.
  2. Source-file scanning — reads Jupyter notebook headers to derive component
     specs, section lists, and quality criteria for chapter experts.
  3. Built-in tool metadata catalog — documentation URLs and API surfaces for
     common Python scientific-computing libraries.

Invoke via:
    python build_team.py ... --enrich        (auto-fill + CSV export)
    enrich.scan_defaults(file_map, manifest) (programmatic, returns findings)
    enrich.auto_enrich(findings, file_map, manifest, project_path)
"""

import shutil  # re-exported so build_team.py can use _enrich.shutil.which(...)

from ._audit import scan_defaults
from ._enrich import ai_enrich, auto_enrich, export_csv, generate_setup_required, load_csv, print_enrich_summary
from ._models import DefaultFinding
from ._tools import (
    _IMPORT_TO_PACKAGE,
    _TOOL_ALIASES,
    build_tool_catalog,
    scan_project_imports,
)

__all__ = [
    "DefaultFinding",
    "scan_defaults",
    "auto_enrich",
    "ai_enrich",
    "export_csv",
    "load_csv",
    "print_enrich_summary",
    "generate_setup_required",
    "scan_project_imports",
    "build_tool_catalog",
    "_IMPORT_TO_PACKAGE",
    "_TOOL_ALIASES",
    "shutil",
]
