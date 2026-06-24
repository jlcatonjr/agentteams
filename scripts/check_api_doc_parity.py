#!/usr/bin/env python3
"""check_api_doc_parity.py — read-only API-reference ↔ module coverage parity.

Verifies that the hand-written API reference pages in ``docs_src/api-reference/``
stay in correspondence with the Python modules in ``agentteams/``. This is the
one conformity axis the existing tooling does NOT cover:

- ``agentteams/man.py`` keeps the CLI man-page (``agentteams.1``) derived from the
  argparse parser, and ``agentteams/stale_detector.py`` flags temporal staleness
  (``STALE_VS_CODE``) and broken markdown links (``BROKEN_REF``) — but neither
  checks that *every public module has a reference page* or that *no page
  documents a module that was deleted*. This script fills exactly that gap.

It is **read-only detection only** (the ``stale_detector`` posture): it never
writes docs. Remediation routes to the doc author. Classifications:

- ``STALE_PAGE``  — a reference page whose backing module no longer exists (hard;
  the only condition ``--check`` fails on by default, because it is unambiguous).
- ``COVERAGE_GAP`` — a public module with no reference page (advisory by default;
  fails only under ``--strict``). Several intentional gaps exist on the current
  tree — so this stays advisory to avoid a false CI break. (The live count is
  printed by the report; it is deliberately not hardcoded here.)
- ``EXEMPT``      — private (``_*``) or curated-internal modules not expected to
  carry a page.

Name normalization: page ``foo-bar.md`` maps to module ``foo_bar`` (dashes ↔
underscores), and to a package ``agentteams/foo_bar/`` when that directory exists.
``index.md`` and ``feature-inventory.md`` are documentation-only pages with no
backing module and are ignored.

Usage::

    python scripts/check_api_doc_parity.py            # human-readable report
    python scripts/check_api_doc_parity.py --check     # exit 1 on any STALE_PAGE
    python scripts/check_api_doc_parity.py --strict    # exit 1 on STALE_PAGE or COVERAGE_GAP
    python scripts/check_api_doc_parity.py --json       # machine-readable report
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Repo layout (this file lives in <root>/scripts/).
_ROOT = Path(__file__).resolve().parent.parent
_PKG_DIR = _ROOT / "agentteams"
_DOCS_DIR = _ROOT / "docs_src" / "api-reference"

# Pages that intentionally have no backing module (documentation/landing pages).
_NON_MODULE_PAGES = frozenset({"index", "feature-inventory"})

# Modules that are intentionally internal and not expected to carry a public
# API-reference page. Anything starting with ``_`` is treated as private too.
#   atomicio, errors — internal infrastructure imported by sibling modules.
#   cli              — the command surface is documented via the generated
#                      man-page (agentteams.1 / man.py), not an api-reference page.
_EXEMPT_MODULES = frozenset({"atomicio", "errors", "cli"})


@dataclass
class ParityResult:
    stale_pages: list[str] = field(default_factory=list)      # page -> no module
    coverage_gaps: list[str] = field(default_factory=list)    # public module -> no page
    exempt: list[str] = field(default_factory=list)           # skipped private/internal
    documented: list[str] = field(default_factory=list)       # module <-> page OK

    def as_dict(self) -> dict[str, list[str]]:
        return asdict(self)


def _module_name_for_page(stem: str) -> str:
    """Normalize a page filename stem to its candidate module name."""
    return stem.replace("-", "_")


def _module_exists(name: str, pkg_dir: Path = _PKG_DIR) -> bool:
    """True if ``<pkg_dir>/<name>.py`` or package ``<pkg_dir>/<name>/`` exists."""
    return (pkg_dir / f"{name}.py").is_file() or (pkg_dir / name / "__init__.py").is_file()


def _public_modules(pkg_dir: Path = _PKG_DIR) -> list[str]:
    """Public modules and subpackages of agentteams (excludes ``_*`` and __init__)."""
    names: set[str] = set()
    for py in pkg_dir.glob("*.py"):
        if py.stem == "__init__" or py.stem.startswith("_"):
            continue
        names.add(py.stem)
    for sub in pkg_dir.iterdir():
        if sub.is_dir() and (sub / "__init__.py").is_file() and not sub.name.startswith("_"):
            names.add(sub.name)
    return sorted(names)


def _page_stems(docs_dir: Path = _DOCS_DIR) -> list[str]:
    return sorted(p.stem for p in docs_dir.glob("*.md"))


def compute_parity(pkg_dir: Path = _PKG_DIR, docs_dir: Path = _DOCS_DIR) -> ParityResult:
    """Compute coverage parity between modules and reference pages (pure)."""
    result = ParityResult()

    documented_modules: set[str] = set()
    for stem in _page_stems(docs_dir):
        if stem in _NON_MODULE_PAGES:
            continue
        mod = _module_name_for_page(stem)
        if _module_exists(mod, pkg_dir):
            result.documented.append(stem)
            documented_modules.add(mod)
        else:
            result.stale_pages.append(stem)

    for mod in _public_modules(pkg_dir):
        if mod in documented_modules:
            continue
        if mod in _EXEMPT_MODULES or mod.startswith("_"):
            result.exempt.append(mod)
        else:
            result.coverage_gaps.append(mod)

    return result


def _format_report(result: ParityResult) -> str:
    lines = ["API DOC PARITY REPORT — agentteams", ""]
    lines.append(f"Pages mapped to a live module : {len(result.documented)}")
    lines.append(f"STALE_PAGE  (page, no module)  : {len(result.stale_pages)}")
    lines.append(f"COVERAGE_GAP (module, no page) : {len(result.coverage_gaps)}")
    lines.append(f"EXEMPT (private/internal)      : {len(result.exempt)}")
    lines.append("")
    if result.stale_pages:
        lines.append("STALE_PAGE — page documents a module that no longer exists (HARD):")
        lines += [f"  - docs_src/api-reference/{s}.md" for s in result.stale_pages]
        lines.append("")
    if result.coverage_gaps:
        lines.append("COVERAGE_GAP — public module with no reference page (advisory):")
        lines += [f"  - agentteams/{m}.py" for m in result.coverage_gaps]
        lines.append("")
    verdict = "FAIL" if result.stale_pages else ("WARN" if result.coverage_gaps else "PASS")
    lines.append(f"OVERALL: {verdict}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check api-reference ↔ module coverage parity.")
    parser.add_argument("--check", action="store_true", help="Exit 1 on any STALE_PAGE.")
    parser.add_argument(
        "--strict", action="store_true", help="Exit 1 on STALE_PAGE or COVERAGE_GAP."
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    result = compute_parity()

    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(_format_report(result))

    if result.stale_pages and (args.check or args.strict):
        return 1
    if result.coverage_gaps and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
