"""Standalone recipe YAML structural validator for Goose recipes.

Invoked via ``agentteams --framework goose --recipe-check [--output <recipes-dir>]``.
Validates every ``.yaml`` file in the recipes directory for structural correctness
(version string, no model: key, non-empty instructions, sub_recipe path resolution).
Writes a ``recipe-check.report.md`` alongside the recipes and exits 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import sys
from pathlib import Path


def run_recipe_check(recipes_dir: Path) -> int:
    """Validate all .yaml recipes in recipes_dir.

    Returns 0 if all recipes pass, 1 if any violations found.
    Prints a human-readable report to stdout and writes recipe-check.report.md.
    """
    from agentteams.frameworks.goose import _validate_recipe_yaml

    recipes = sorted(recipes_dir.glob("*.yaml"))
    if not recipes:
        print(f"  recipe-check: no .yaml files found in {recipes_dir}")
        return 0

    results: dict[str, list[str]] = {}
    for recipe in recipes:
        try:
            text = recipe.read_text(encoding="utf-8")
        except OSError as exc:
            results[recipe.name] = [f"could not read file: {exc}"]
            continue
        violations = _validate_recipe_yaml(text, recipes_dir)
        if violations:
            results[recipe.name] = violations

    total = len(recipes)
    failed = len(results)
    passed = total - failed
    ok = failed == 0

    status_line = "PASS" if ok else f"FAIL — {failed} recipe(s) have violations"
    report_lines = [
        "# Recipe Check Report",
        "",
        f"Result: {status_line}",
        f"Checked: {total} recipe(s)  |  Passed: {passed}  |  Failed: {failed}",
        "",
    ]
    for recipe in recipes:
        report_lines.append(f"## {recipe.name}")
        if recipe.name in results:
            for v in results[recipe.name]:
                report_lines.append(f"- FAIL: {v}")
        else:
            report_lines.append("- PASS")
        report_lines.append("")

    report_text = "\n".join(report_lines)
    report_path = recipes_dir / "recipe-check.report.md"
    try:
        report_path.write_text(report_text, encoding="utf-8")
    except OSError as exc:
        print(f"  Warning: could not write {report_path}: {exc}", file=sys.stderr)

    print(report_text)
    return 0 if ok else 1
